"""The glue between the contract and DuckDB, tested without Soda or GCS.

cd runtime && uv run --with pytest pytest ../examples
"""

import importlib.util
from pathlib import Path

import duckdb
import pytest
import yaml

spec = importlib.util.spec_from_file_location("verify", Path(__file__).with_name("verify.py"))
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)

CONTRACT = yaml.safe_load(Path(__file__).with_name("contract.yml").read_text())


class TestContractToDuckDB:
    def test_given_contract_when_naming_table_then_last_part_of_dataset(self) -> None:
        # when / then
        assert verify.table_name(CONTRACT) == "customers"

    def test_given_other_data_source_when_naming_table_then_refused(self) -> None:
        # when / then
        with pytest.raises(ValueError):
            verify.table_name({"dataset": "warehouse/main/customers"})

    def test_given_csv_when_reading_with_contract_types_then_bad_lines_are_rejected_not_fatal(
        self, tmp_path: Path
    ) -> None:
        # given: a good line, a line with text in a number, a line with an extra column
        csv = tmp_path / "customers.csv"
        csv.write_text(
            "customer_id,name,email,country,signup_date,lifetime_value\n"
            "1,Ada,ada@example.com,FR,2020-01-01,12.50\n"
            "2,Bob,bob@example.com,DE,2020-01-02,n/a\n"
            "3,Cy,cy@example.com,ES,2020-01-03,1.00,extra\n"
        )
        con = duckdb.connect()

        # when
        con.sql(verify.read_csv_sql(CONTRACT, str(csv)))

        # then
        assert con.sql("SELECT customer_id FROM customers").fetchall() == [(1,)]
        assert con.sql("SELECT count(*) FROM reject_errors").fetchone()[0] == 2
        assert dict(con.sql("DESCRIBE customers").fetchall()[i][:2] for i in range(6))["signup_date"] == "DATE"

    def test_given_column_without_type_when_reading_then_refused(self) -> None:
        # when / then
        with pytest.raises(ValueError):
            verify.read_csv_sql({"dataset": "duckless/main/t", "columns": [{"name": "a", "data_type": None}]}, "x.csv")
