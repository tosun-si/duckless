"""Copy a large GCS object to the machine's local SSD, with many reads in parallel.

One read stream from GCS gives about 100 MB/s. Reading one big file in place (`read_csv('gs://…')`)
is limited by how many ranges are fetched at once; fetching 64 ranges in parallel into a local file
reached 1.3 GB/s on n2-highmem-32, and DuckDB then reads the local file at disk speed: a 12 GB CSV
loaded in 15 s instead of 130 s. Worth it for big files read in full (CSV, JSON); Parquet read in
place already skips what a query does not need.
"""

import concurrent.futures
import os
from pathlib import Path

CHUNK_BYTES = 64 * 1024 * 1024
PARALLEL_READS = 64


def ranges(size: int, chunk: int = CHUNK_BYTES) -> list[tuple[int, int]]:
    """(offset, length) of each chunk of an object of `size` bytes."""
    return [(offset, min(chunk, size - offset)) for offset in range(0, size, chunk)]


def local_path(uri: str, directory: Path) -> Path:
    """Where `gs://bucket/a/b.csv` lands: `<directory>/bucket/a/b.csv`, keeping the extension DuckDB reads."""
    if not uri.startswith("gs://"):
        raise ValueError(f"'{uri}' is not a gs:// URI")
    return directory / uri.removeprefix("gs://")


def stage_locally(uri: str, directory: str | Path | None = None, parallel_reads: int = PARALLEL_READS) -> str:
    """Download `gs://…` to local disk in parallel ranged reads; returns the local path.

        journal = stage_locally("gs://bucket/journal/2026-09.csv")
        con.sql(f"CREATE TABLE journal AS SELECT * FROM read_csv('{journal}', delim = ';')")

    On Cloud Batch the file goes to the local SSD (the spill directory's disk). On Cloud Run there is
    no disk: /tmp is memory, so the file counts against the job's memory.
    """
    import pyarrow.fs as pafs  # GCS client with the job's credentials (ADC)

    base = Path(directory or os.environ.get("DUCKLESS_SCRATCH_DIR", "/mnt/disks/scratch"))
    if not base.is_dir() or not os.access(base, os.W_OK):
        base = Path("/tmp")
    target = local_path(uri, base / "staged")
    target.parent.mkdir(parents=True, exist_ok=True)

    fs = pafs.GcsFileSystem()
    source = uri.removeprefix("gs://")
    size = fs.get_file_info(source).size
    with open(target, "wb") as out:
        out.truncate(size)

    def copy(span: tuple[int, int]) -> None:
        offset, length = span
        with fs.open_input_file(source) as src, open(target, "r+b") as out:
            out.seek(offset)
            out.write(src.read_at(length, offset))

    with concurrent.futures.ThreadPoolExecutor(parallel_reads) as pool:
        list(pool.map(copy, ranges(size)))
    return str(target)
