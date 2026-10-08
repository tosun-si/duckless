# 02 · Late corrections with DuckLake

Data that changes after it was loaded: orders arrive as daily CSV drops, and days later a
newline-delimited JSON file says some were cancelled and one customer asked to be erased. With
plain Parquet files that means rewriting files by hand; a
[DuckLake](https://tosun-si.github.io/duckless/guides/ducklake/) table takes `UPDATE` and
`DELETE` as they are, keeps every version, and lets you look back.

> If your data is only ever appended or fully recomputed, you don't need DuckLake:
> [example 01](../01-daily-marts/), plain Parquet with `OVERWRITE`, is cheaper and simpler.

Needs an installation with a catalog (`duckless init --ducklake`) and the data of `00-seed`.

```bash
duckless run examples/02-ducklake-incremental/load_day.sql -m n2-standard-4 -e LOAD_DATE=1998-07-01
duckless run examples/02-ducklake-incremental/load_day.sql -m n2-standard-4 -e LOAD_DATE=1998-07-02
duckless run examples/02-ducklake-incremental/load_day.sql -m n2-standard-4 -e LOAD_DATE=1998-07-02   # again
duckless run examples/02-ducklake-incremental/apply_corrections.sql -m n2-standard-4
duckless run examples/02-ducklake-incremental/history.sql -m n2-standard-4 -e VERSION=4
```

## What to look at

- **CSV in, table out.** `load_day.sql` reads the day's CSV (`read_csv` infers the columns and
  types) and the first run creates `lake.orders` from it.
- **Re-running a day replaces it.** Delete then insert, in one transaction: loading `1998-07-02`
  twice still gives one copy of the day.
- **Corrections in place.** `apply_corrections.sql` reads the JSON file, cancels orders with an
  `UPDATE` and erases the customer's orders with a `DELETE`, in one transaction. No file is
  rewritten by you; DuckLake records what changed.
- **Every step is a snapshot**, listed by `history.sql`:

  | Snapshot | Changes |
  | --- | --- |
  | 0 | schema created |
  | 1 | table `orders` created |
  | 2 | 1998-07-01 loaded |
  | 3 | 1998-07-02 loaded |
  | 4 | 1998-07-02 loaded again (deleted and inserted) |
  | 5 | corrections (updated and deleted) |

- **Time travel.** `history.sql` compares `lake.orders AT (VERSION => 4)`, the table before the
  corrections, with the table now. With `SCALE=1`:

  | State | Orders | Cancelled |
  | --- | --- | --- |
  | before (version 4) | 1,267 | 0 |
  | now | 1,266 | 123 |

  One order fewer (the erased customer's), 123 cancelled. `VERSION` must be a literal, which is
  why it is passed with `-e`.

`duckless logs <job-id>` shows each statement's first rows, the snapshot list included;
`duckless result <job-id>` shows the last one.
