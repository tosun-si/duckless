"""The business logic is a plain function: tested on a few values, without BigQuery or GCS.

cd runtime && uv run --with pytest pytest ../examples
"""

import importlib.util
from pathlib import Path

import pyarrow as pa

spec = importlib.util.spec_from_file_location("score_orders", Path(__file__).with_name("score_orders.py"))
score_orders = importlib.util.module_from_spec(spec)
spec.loader.exec_module(score_orders)


class TestOrderScore:
    def test_given_same_price_when_scoring_then_urgent_scores_higher_than_low(self) -> None:
        # given
        prices = pa.array([1000.0, 1000.0])
        priorities = pa.array(["1-URGENT", "5-LOW"])

        # when
        urgent, low = score_orders.order_score(prices, priorities).to_pylist()

        # then
        assert urgent > low

    def test_given_unknown_priority_when_scoring_then_neutral_weight(self) -> None:
        # when
        scores = score_orders.order_score(pa.array([0.0, 99.0]), pa.array(["?", "?"])).to_pylist()

        # then
        assert scores == [0.0, 4.605]
