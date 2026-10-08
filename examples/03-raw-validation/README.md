# 03 · Validating raw files

A raw CSV arrives from a source system. Before anything uses it: does every line parse, are the
keys unique, are emails, countries, dates and amounts valid? This example checks a file against
rules declared in YAML and writes every error to GCS.

**Pydantic checks the rules, DuckDB checks the data.** The rules file is validated by Pydantic
models before a single line is read: an unknown rule, a misspelt key, a column that does not
exist or a broken regex stops the job at once. Each rule then becomes one SQL predicate, and
DuckDB runs them all over the whole file, in parallel, without any Python touching a row. That
is what keeps it fast on large files: validating row by row in Python (a Pydantic model per
line) runs thousands of rows per second; the same checks in SQL run millions.

```bash
# 1. a raw file with errors in it (1 million lines here; try 100 million on a bigger machine)
duckless run examples/03-raw-validation/make_raw.sql -m n2-standard-4 -e ROWS=1000000

# 2. the rules, next to the data
gcloud storage cp examples/03-raw-validation/rules.yaml gs://$DUCKLESS_BUCKET/examples/validation/

# 3. validate
duckless run examples/03-raw-validation/validate.py -m n2-standard-8 \
  -e RULES=gs://$DUCKLESS_BUCKET/examples/validation/rules.yaml
duckless logs <job-id>   # the validation_summary line
```

## The rules

```yaml
rules:
  - name: customer_id_unique
    unique: customer_id
  - name: email_format
    column: email
    matches: '^[^@\s]+@[^@\s]+\.[a-z]{2,}$'
  - name: country_known
    column: country
    one_of: [FR, DE, ES, IT, GB, US, BE, NL, PT, CH]
  - name: lifetime_value_positive
    check: lifetime_value >= 0       # any SQL condition over the columns
```

| Kind | Fails when |
| --- | --- |
| `unique: <column>` | the value appears more than once (every copy is reported) |
| `not_null: <column>` | the value is missing or blank |
| `column` + `matches` | the value does not match the regular expression |
| `column` + `one_of` | the value is not in the list |
| `check: <SQL condition>` | the condition is false or unknown |

On top of the rules, `source.columns` is the expected schema: a line that does not fit it (text
in a number, an impossible date, an extra column) is caught by DuckDB's CSV reader
(`store_rejects`) and reported as `unparsable_line`, with the line number and the reason,
instead of failing the whole read.

## The report

| Written to `report` | |
| --- | --- |
| `errors/*.parquet` | one row per error: `rule`, `record_key` (the key, or the line number for unparsable lines), `detail` |
| `summary.json` | lines, lines with errors, error rate, errors per rule, time spent checking |

The job **fails** when the share of lines with errors is above `max_error_rate`, so an
orchestrator can stop the pipeline there. The report is written either way.

With 1 million lines (`ROWS=1000000`), the file has about 6,400 bad lines (0.64%, under the 1%
threshold): 452 unparsable lines, then about 1,000 per rule, and 1,956 rows sharing a key.

## How fast

Measured in `europe-west1`. "Checking" is reading the CSV, applying the six rules and collecting
the errors; the whole job adds the startup and writing the report.

| File | Machine | Checking | Whole job | Peak memory |
| --- | --- | --- | --- | --- |
| 1 million lines, 72 MB | Cloud Run Jobs, 8 vCPU | 1.4 s | 16 s | 0.35 GB |
| 100 million lines, 7.8 GB | Cloud Run Jobs, 8 vCPU | 125 s | 2 min 32 | 13 GB |
| 100 million lines, 7.8 GB | `n2-highmem-16` (Cloud Batch) | 90 s | 1 min 37 | 14 GB |
| 100 million lines, 7.8 GB | `n2-highmem-32` Spot (Cloud Batch) | 63 s | 1 min 10 | 14 GB |

About 1.6 million lines per second on 32 vCPUs, for 634,138 errors found in 100 million lines.
Adding a rule adds one predicate to the same pass over the data, not another read.

## Tests

The rules are tested on DuckDB in memory, the same engine as on the runner, without any cloud:

```bash
cd runtime && uv run --with pytest pytest ../examples
```

## Going further

- **BigQuery tables**: export them to Parquet (`EXPORT DATA`) and point `source.path` at the
  files, or read them directly with DuckDB's community `bigquery` extension in an image built
  `FROM` the runner.
- **Rules that need Python**: run them on the suspicious rows only (the `errors` table), never on
  the whole file.
