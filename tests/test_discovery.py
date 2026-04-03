"""Tests for discovery/candidates.py — schema validation, staging, commit."""

import json
import os
import tempfile

import pandas as pd
import pytest

from discovery.candidates import (
    validate_discovery_output,
    stage_candidates,
    read_staged_candidates,
    write_discovery_input,
    VALID_TRIGGERS,
)
from config import DATA_DIR


def _make_discovery_json(candidates, date="2026-03-28"):
    """Write a temporary discovery output JSON and return the path."""
    data = {"date": date, "candidates": candidates}
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    return path


def _sample_candidate(**overrides):
    base = {
        "company": "TestCorp Inc",
        "ticker": "TSTC",
        "exchange": "NASDAQ",
        "market_cap": 5000000000,
        "sector": "Tech",
        "subsector": "SaaS",
        "trigger": "IPO",
        "peers": ["MSFT", "GOOG"],
        "reason": "New IPO in tech sector",
        "business_summary": "We build test software.",
        "approved": False,
    }
    base.update(overrides)
    return base


class TestValidateDiscoveryOutput:
    def test_valid_candidate(self):
        path = _make_discovery_json([_sample_candidate()])
        try:
            valid, errors = validate_discovery_output(path)
            assert len(valid) == 1
            assert len(errors) == 0
        finally:
            os.unlink(path)

    def test_missing_file(self):
        valid, errors = validate_discovery_output("/nonexistent/path.json")
        assert len(valid) == 0
        assert len(errors) == 1
        assert "not found" in errors[0]

    def test_invalid_json(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as f:
            f.write("{not valid json")
        try:
            valid, errors = validate_discovery_output(path)
            assert len(valid) == 0
            assert len(errors) == 1
        finally:
            os.unlink(path)

    def test_missing_required_fields(self):
        path = _make_discovery_json([{"company": "Foo"}])
        try:
            valid, errors = validate_discovery_output(path)
            assert len(valid) == 0
            assert len(errors) == 1
            assert "missing required" in errors[0]
        finally:
            os.unlink(path)

    def test_invalid_trigger(self):
        path = _make_discovery_json([_sample_candidate(trigger="BadTrigger")])
        try:
            valid, errors = validate_discovery_output(path)
            assert len(valid) == 0
            assert len(errors) == 1
            assert "invalid trigger" in errors[0]
        finally:
            os.unlink(path)

    def test_duplicate_ticker_rejected(self):
        # ISRG is in the coverage universe
        path = _make_discovery_json([_sample_candidate(ticker="ISRG")])
        try:
            valid, errors = validate_discovery_output(path)
            assert len(valid) == 0
            assert len(errors) == 1
            assert "already in coverage" in errors[0]
        finally:
            os.unlink(path)

    def test_multiple_candidates_mixed(self):
        path = _make_discovery_json([
            _sample_candidate(ticker="NEWCO1", company="NewCo One"),
            _sample_candidate(ticker="ISRG", company="Intuitive Surgical"),  # dupe
            _sample_candidate(ticker="NEWCO2", company="NewCo Two"),
        ])
        try:
            valid, errors = validate_discovery_output(path)
            assert len(valid) == 2
            assert len(errors) == 1
        finally:
            os.unlink(path)


class TestStaging:
    def test_stage_and_read(self):
        candidates = [
            _sample_candidate(ticker="AAA", approved=True),
            _sample_candidate(ticker="BBB", approved=False),
        ]
        fd, path = tempfile.mkstemp(suffix=".csv")
        os.close(fd)
        try:
            stage_candidates(candidates, staging_path=path)
            approved = read_staged_candidates(path)
            assert len(approved) == 1
            assert approved[0]["ticker"] == "AAA"
        finally:
            os.unlink(path)

    def test_read_nonexistent(self):
        result = read_staged_candidates("/nonexistent.csv")
        assert result == []


class TestWriteDiscoveryInput:
    def test_writes_json(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            from config import CSV_PATH
            result = write_discovery_input(csv_path=CSV_PATH, output_path=path)
            assert os.path.exists(result)
            with open(result) as f:
                data = json.load(f)
            assert "tickers" in data
            assert "total_tickers" in data
            assert data["total_tickers"] > 0
        finally:
            os.unlink(path)
