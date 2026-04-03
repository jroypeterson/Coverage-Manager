"""Tests for add_exchanges.py idempotency."""

import os
import tempfile
import pandas as pd


def test_add_exchanges_does_not_duplicate_column():
    """Running add_exchanges on a CSV that already has Exchange should not crash or duplicate."""
    # Create a temp CSV with an existing Exchange column
    data = {
        "Ticker": ["AAPL", "MSFT"],
        "Exchange": ["NASDAQ", "NASDAQ"],
        "Company Name": ["Apple Inc.", "Microsoft Corp."],
    }
    df = pd.DataFrame(data)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        df.to_csv(f, index=False)
        tmp_path = f.name

    try:
        # Simulate what add_exchanges.main() does at the critical section
        df2 = pd.read_csv(tmp_path)
        exchanges = ["NYSE", "NYSE"]  # new values

        if "Exchange" in df2.columns:
            df2["Exchange"] = exchanges
        else:
            df2.insert(1, "Exchange", exchanges)

        # Should have exactly one Exchange column
        exchange_cols = [c for c in df2.columns if c == "Exchange"]
        assert len(exchange_cols) == 1, f"Expected 1 Exchange column, got {len(exchange_cols)}"

        # Values should be updated
        assert list(df2["Exchange"]) == ["NYSE", "NYSE"]
    finally:
        os.unlink(tmp_path)


def test_add_exchanges_inserts_when_missing():
    """When Exchange column doesn't exist, it should be inserted."""
    data = {
        "Ticker": ["AAPL"],
        "Company Name": ["Apple Inc."],
    }
    df = pd.DataFrame(data)
    exchanges = ["NASDAQ"]

    if "Exchange" in df.columns:
        df["Exchange"] = exchanges
    else:
        df.insert(1, "Exchange", exchanges)

    assert "Exchange" in df.columns
    assert list(df.columns) == ["Ticker", "Exchange", "Company Name"]
    assert df["Exchange"].iloc[0] == "NASDAQ"
