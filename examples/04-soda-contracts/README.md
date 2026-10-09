# 04 · Soda data contracts on DuckLess

[Soda](https://soda.io) describes data quality as a **data contract**: the columns of a dataset
and the checks they must pass, in YAML. This example verifies a Soda v4 contract on the raw file
of [example 03](../03-raw-validation/), with Soda's DuckDB connector, on a DuckLess machine.

Soda decides *what* is checked and how results are reported; DuckDB on DuckLess does the work,
next to the file on GCS. Nothing is loaded into a warehouse first, and the check runs on a machine
billed by the minute instead of by the bytes a warehouse would scan.

## What runs

1. `verify.py` reads the CSV with the **contract's columns and types** (`read_csv(…, store_rejects
   = true)`): lines that do not fit are counted as unparsable instead of failing the read.
2. Soda verifies the contract **through the job's DuckDB connection** (`duckless_runtime.connect()`),
   so every check is SQL that DuckDB runs on the machine.
3. The results go to GCS (`report.json`) and to the logs; the job **fails when the contract fails**.

The [contract](contract.yml) holds the same rules as example 03, in Soda's language:

```yaml
columns:
  - name: customer_id
    data_type: bigint
    checks:
      - missing:
      - duplicate:                 # no tolerance
  - name: email
    data_type: varchar
    checks:
      - invalid:
          valid_format: {name: email, regex: '^[^@\s]+@[^@\s]+\.[a-z]{2,}$'}
          threshold: {metric: percent, must_be_less_than: 1}
checks:
  - failed_rows:
      name: signup_not_in_future
      expression: signup_date > current_date
      threshold: {level: warn}     # reported, does not fail
```

## Run it

Soda is not in the runner image: build an image `FROM` it with Soda added ([Dockerfile](Dockerfile)),
in a registry the runner account can read.

```bash
# 1. the image (Artifact Registry: give the runner account roles/artifactregistry.reader on the repository)
docker buildx build --platform linux/amd64 -t europe-west1-docker.pkg.dev/$DUCKLESS_PROJECT/jobs/soda:4.26 --push examples/04-soda-contracts

# 2. the raw file of example 03, and the contract next to it
duckless run examples/03-raw-validation/make_raw.sql -m n2-standard-4 -e ROWS=1000000
gcloud storage cp examples/04-soda-contracts/contract.yml gs://$DUCKLESS_BUCKET/examples/soda/

# 3. verify
duckless exec --image europe-west1-docker.pkg.dev/$DUCKLESS_PROJECT/jobs/soda:4.26 -m n2-standard-8 \
  -e SOURCE=gs://$DUCKLESS_BUCKET/examples/raw/validation/customers.csv \
  -e CONTRACT=gs://$DUCKLESS_BUCKET/examples/soda/contract.yml \
  -- python /usr/local/src/app/verify.py
duckless logs <job-id>    # one soda_check line per check, then soda_contract
```

With the data of example 03, the contract fails on duplicate keys (no tolerance), passes the
checks that allow 1% of bad values (about 0.1% each), warns on signup dates in the future, and
reports 452 unparsable lines per million.

## How fast

Measured in `europe-west1` with the file of example 03. Loading is reading and parsing the CSV
into a table; the nine Soda checks then run as SQL aggregations over it.

| File | Machine | Loading | Soda checks | Whole job |
| --- | --- | --- | --- | --- |
| 1 million lines | Cloud Run Jobs, 8 vCPU | 1.9 s | 0.15 s | 46 s |
| 100 million lines, 7.8 GB | `n2-highmem-32` Spot (Cloud Batch) | 85 s | 1.8 s | 1 min 36 |
| 100 million lines, 7.8 GB | Cloud Run Jobs, 8 vCPU | 124 s | 5.9 s | 2 min 56 |

The checks cost almost nothing once the data is in DuckDB: the time goes into reading the raw
file, as in example 03. On 100 million lines Soda found the same errors as example 03 (97,857
duplicated keys, about 0.1% bad values per rule, 45,375 unparsable lines).

## Things learned writing it (Soda 4.26)

- Soda's DuckDB connector accepts an existing connection (`duckdb_connection`) and **closes it**
  when it is done: give it `con.cursor()`, a second connection to the same database.
- Contracts compare column types by name: `data_type: decimal`, not `decimal(12,2)` (the CSV is
  then read as `DECIMAL(18,3)`).
- `soda data-source create -t duckdb` only generates Postgres configurations for now; the data
  source is built in code here.

## Soda or example 03?

| | Example 03 (rules in YAML, ~150 lines of Python) | This example (Soda v4) |
| --- | --- | --- |
| Rules language | yours, minimal | Soda's: thresholds, warn levels, schema, freshness, reference data… |
| Results | Parquet of every error + summary on GCS | per-check outcomes; Soda Cloud (paid) for history, alerts, incidents |
| Dependencies | none beyond the runner | an image with Soda (~80 MB) |
| Error rows | every failing row, with its key | counts per check (failed rows on demand) |

Pick Soda when the team adopts it as its quality standard, or wants Soda Cloud; example 03 when
a few rules and a report of every bad row are enough.

## Tests

The glue between the contract and DuckDB (table name, typed CSV read, rejected lines) is tested
without Soda or GCS: `cd runtime && uv run --with pytest pytest ../examples`.
