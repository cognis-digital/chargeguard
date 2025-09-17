"""Smoke tests for CHARGEGUARD. No network; runs against the bundled demo."""
import json
import os
import subprocess
import sys

import pytest

from chargeguard import (
    TOOL_NAME,
    TOOL_VERSION,
    analyze_file,
    analyze_records,
    load_records,
    DEFAULT_THRESHOLDS,
)
from chargeguard.cli import main

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEMO = os.path.join(ROOT, "demos", "01-basic", "feed.csv")


def test_exports():
    assert TOOL_NAME == "chargeguard"
    assert isinstance(TOOL_VERSION, str) and TOOL_VERSION
    assert len(DEFAULT_THRESHOLDS) >= 3


def test_demo_file_exists():
    assert os.path.isfile(DEMO)


def _synthetic_feed():
    # 1000 settled, 12 chargebacks => 1.2% > 0.9% breach ceiling
    rows = []
    for i in range(1000):
        rows.append({"merchant_id": "m1", "type": "settled", "amount": "10",
                     "date": "2026-06-01"})
    for i in range(12):
        rows.append({"merchant_id": "m1", "type": "chargeback", "amount": "10",
                     "date": "2026-06-02"})
    return rows


def test_ratio_math_is_real():
    rows = _synthetic_feed()
    report = analyze_records(rows)
    m1 = next(w for w in report.windows if w.merchant_id == "m1")
    assert m1.settled_count == 1000
    assert m1.chargeback_count == 12
    assert abs(m1.cb_ratio - 0.012) < 1e-9
    # 1.2% trips the 0.90% standard breach
    breach_names = {f.threshold for f in report.breaches}
    assert "VAMP standard" in breach_names


def test_clean_merchant_has_no_findings():
    rows = [{"merchant_id": "clean", "type": "settled", "amount": "5",
             "date": "2026-06-01"} for _ in range(1000)]
    rows.append({"merchant_id": "clean", "type": "chargeback", "amount": "5",
                 "date": "2026-06-02"})  # 0.1% — well under ceilings
    report = analyze_records(rows)
    assert report.findings == []


def test_load_records_csv_and_json_equivalent():
    csv_text = "merchant_id,type,amount,date\nm,settled,10,2026-06-01\n"
    json_text = json.dumps([{"merchant_id": "m", "type": "settled",
                             "amount": 10, "date": "2026-06-01"}])
    a = load_records(csv_text)
    b = load_records(json_text)
    assert a[0]["merchant_id"] == b[0]["merchant_id"] == "m"
    assert a[0]["amount"] == b[0]["amount"] == 10.0


def test_analyze_demo_file_has_breaches():
    report = analyze_file(DEMO)
    assert len(report.breaches) >= 2
    merchants = {w.merchant_id for w in report.windows}
    assert {"acme_co", "good_shop", "risky_mart"} <= merchants
    # good_shop is clean
    gs_findings = [f for f in report.findings if f.merchant_id == "good_shop"]
    assert gs_findings == []


def test_window_filtering():
    rows = [
        {"merchant_id": "m", "type": "settled", "amount": "1", "date": "2026-01-01"},
        {"merchant_id": "m", "type": "settled", "amount": "1", "date": "2026-06-01"},
        {"merchant_id": "m", "type": "chargeback", "amount": "1", "date": "2026-06-02"},
    ]
    full = analyze_records(rows)
    win = analyze_records(rows, window_days=30)
    m_full = next(w for w in full.windows if w.merchant_id == "m")
    m_win = next(w for w in win.windows if w.merchant_id == "m")
    assert m_full.settled_count == 2
    assert m_win.settled_count == 1  # Jan record dropped by window


def test_cli_returns_nonzero_on_breach():
    rc = main(["scan", DEMO])
    assert rc == 1  # breaches present -> CI gate trips


def test_cli_json_output(capsys):
    rc = main(["scan", DEMO, "--format", "json"])
    assert rc == 1
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["summary"]["breaches"] >= 2
    assert "merchants" in data and "findings" in data


def test_cli_fail_on_never_exits_zero():
    rc = main(["scan", DEMO, "--fail-on", "never"])
    assert rc == 0


def test_cli_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert TOOL_VERSION in out


def test_module_entrypoint_runs():
    proc = subprocess.run(
        [sys.executable, "-m", "chargeguard", "scan", DEMO, "--format", "json"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert proc.returncode == 1
    data = json.loads(proc.stdout)
    assert data["summary"]["breaches"] >= 2
