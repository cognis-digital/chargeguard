"""Hardening tests: error paths, edge cases, and input validation."""
from __future__ import annotations

import json

import pytest

from chargeguard.cli import main
from chargeguard.core import (
    MerchantWindow,
    analyze_records,
    load_records,
    analyze_file,
)


# ---------------------------------------------------------------------------
# CLI: missing file -> exit 2
# ---------------------------------------------------------------------------

def test_cli_missing_file_returns_2(tmp_path):
    missing = str(tmp_path / "no_such_feed.csv")
    rc = main(["scan", missing])
    assert rc == 2


def test_cli_missing_file_stderr(tmp_path, capsys):
    missing = str(tmp_path / "no_such_feed.csv")
    main(["scan", missing])
    err = capsys.readouterr().err
    assert "not found" in err.lower() or "error" in err.lower()


# ---------------------------------------------------------------------------
# CLI: malformed JSON -> exit 2
# ---------------------------------------------------------------------------

def test_cli_malformed_json_returns_2(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ this is not valid json }", encoding="utf-8")
    rc = main(["scan", str(bad), "--input-format", "json"])
    assert rc == 2


def test_cli_malformed_json_stderr(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{ this is not valid json }", encoding="utf-8")
    main(["scan", str(bad), "--input-format", "json"])
    err = capsys.readouterr().err
    assert "error" in err.lower()


# ---------------------------------------------------------------------------
# CLI: negative --window-days -> exit 2
# ---------------------------------------------------------------------------

def test_cli_negative_window_days_returns_2(tmp_path):
    feed = tmp_path / "feed.csv"
    feed.write_text("merchant_id,type,amount,date\nm,settled,10,2026-06-01\n", encoding="utf-8")
    rc = main(["scan", str(feed), "--window-days", "-5"])
    assert rc == 2


def test_cli_zero_window_days_returns_2(tmp_path):
    feed = tmp_path / "feed.csv"
    feed.write_text("merchant_id,type,amount,date\nm,settled,10,2026-06-01\n", encoding="utf-8")
    rc = main(["scan", str(feed), "--window-days", "0"])
    assert rc == 2


# ---------------------------------------------------------------------------
# Core: empty feed file -> ValueError
# ---------------------------------------------------------------------------

def test_analyze_file_empty_raises(tmp_path):
    empty = tmp_path / "empty.csv"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        analyze_file(str(empty))


# ---------------------------------------------------------------------------
# Core: empty records -> clean report (no crash)
# ---------------------------------------------------------------------------

def test_analyze_records_empty_list():
    report = analyze_records([])
    assert report.windows == []
    assert report.findings == []
    assert report.records_total == 0


# ---------------------------------------------------------------------------
# Core: feed with only chargebacks (no settled) -> no division by zero
# ---------------------------------------------------------------------------

def test_only_chargebacks_no_division_by_zero():
    rows = [
        {"merchant_id": "m", "type": "chargeback", "amount": "50", "date": "2026-06-01"}
        for _ in range(10)
    ]
    report = analyze_records(rows)
    # merchant present but settled_count=0, so it's skipped for threshold eval
    assert report.findings == []
    m = next((w for w in report.windows if w.merchant_id == "m"), None)
    assert m is not None
    assert m.settled_count == 0
    assert m.cb_ratio == 0.0


# ---------------------------------------------------------------------------
# Core: JSON feed with non-dict items -> clear ValueError
# ---------------------------------------------------------------------------

def test_load_records_json_non_dict_items():
    bad_json = json.dumps([1, 2, 3])
    with pytest.raises(ValueError, match="non-object"):
        load_records(bad_json, fmt="json")


# ---------------------------------------------------------------------------
# Core: JSON feed that is a bare scalar -> clear ValueError
# ---------------------------------------------------------------------------

def test_load_records_json_scalar():
    with pytest.raises(ValueError, match="list"):
        load_records('"just a string"', fmt="json")


# ---------------------------------------------------------------------------
# Core: MerchantWindow.metric with unknown name -> ValueError
# ---------------------------------------------------------------------------

def test_merchant_window_metric_unknown():
    w = MerchantWindow(merchant_id="x")
    with pytest.raises(ValueError, match="unknown metric"):
        w.metric("nonexistent_field")


# ---------------------------------------------------------------------------
# Core: records with no merchant_id are silently skipped
# ---------------------------------------------------------------------------

def test_records_without_merchant_id_skipped():
    csv_text = "merchant_id,type,amount\n,settled,10\n,chargeback,5\n"
    records = load_records(csv_text, fmt="csv")
    assert records == []


# ---------------------------------------------------------------------------
# Core: amounts with currency symbols parse correctly
# ---------------------------------------------------------------------------

def test_amount_with_currency_symbol():
    # CSV value must be quoted when it contains commas; the parser strips $ and ,
    csv_text = 'merchant_id,type,amount,date\nm,settled,"$1,234.56",2026-06-01\n'
    records = load_records(csv_text, fmt="csv")
    assert len(records) == 1
    assert abs(records[0]["amount"] - 1234.56) < 1e-6
