"""CHARGEGUARD core engine.

Parses a transaction/chargeback feed (CSV or JSON), aggregates per-merchant over
a rolling time window, and computes the two ratios card networks actually monitor:

  * Chargeback (dispute) RATIO = chargebacks / settled transactions (by COUNT)
  * Chargeback (dispute) RATIO by AMOUNT = chargeback $ / settled $

It then compares each merchant against tiered thresholds modeled on Visa's VAMP /
legacy VDMP program (Early-Warning / Standard / Excessive) plus a configurable
fraud-rate ceiling. Breaches and near-breaches are emitted as Findings.

No third-party dependencies. No network calls.

Feed schema (CSV header or JSON list of objects). Recognized fields:
  merchant_id   (str)   required
  type          (str)   one of: sale|settled|chargeback|dispute|refund|fraud
                        (a row's `type` decides which bucket it counts toward)
  amount        (float) transaction amount (>= 0); defaults to 0 if absent
  date          (str)   ISO-8601 date or datetime (used for windowing)

A "settled" transaction is the denominator. A row of type chargeback/dispute is
counted as a chargeback (and as fraud if also flagged). type=fraud rows count
toward the fraud numerator. type=refund rows are ignored for ratios.
"""
from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Threshold:
    """A named ceiling on a ratio. `level` orders severity for reporting."""
    name: str
    metric: str            # "cb_ratio" | "cb_ratio_amount" | "fraud_ratio"
    ceiling: float         # ratio (0..1), e.g. 0.009 == 0.90%
    level: str             # "warning" | "breach"
    note: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


# Modeled on Visa's published monitoring tiers. The dispute-count ratio ceilings
# (0.65% early-warning / 0.90% standard / 1.5% excessive) mirror the VAMP /
# legacy VDMP structure; fraud ceiling mirrors common acquirer fraud limits.
DEFAULT_THRESHOLDS: List[Threshold] = [
    Threshold("VAMP early-warning", "cb_ratio", 0.0065, "warning",
              "Approaching Visa monitoring; tighten risk rules now."),
    Threshold("VAMP standard", "cb_ratio", 0.0090, "breach",
              "At/over Visa standard dispute-ratio ceiling (0.90%)."),
    Threshold("VAMP excessive", "cb_ratio", 0.0150, "breach",
              "Excessive tier (1.50%) — fines/remediation likely."),
    Threshold("Dispute $ ratio", "cb_ratio_amount", 0.0100, "warning",
              "Chargeback dollar share elevated."),
    Threshold("Fraud ratio", "fraud_ratio", 0.0090, "breach",
              "Fraud-to-sales ratio at/over typical acquirer ceiling (0.90%)."),
]


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

_CHARGEBACK_TYPES = {"chargeback", "dispute", "cb"}
_SETTLED_TYPES = {"sale", "settled", "settlement", "capture", "payment"}
_FRAUD_TYPES = {"fraud", "fraudulent"}


@dataclass
class MerchantWindow:
    """Per-merchant aggregates within the analysis window."""
    merchant_id: str
    settled_count: int = 0
    settled_amount: float = 0.0
    chargeback_count: int = 0
    chargeback_amount: float = 0.0
    fraud_count: int = 0
    fraud_amount: float = 0.0

    @property
    def cb_ratio(self) -> float:
        return self.chargeback_count / self.settled_count if self.settled_count else 0.0

    @property
    def cb_ratio_amount(self) -> float:
        return self.chargeback_amount / self.settled_amount if self.settled_amount else 0.0

    @property
    def fraud_ratio(self) -> float:
        return self.fraud_count / self.settled_count if self.settled_count else 0.0

    def metric(self, name: str) -> float:
        return getattr(self, name)

    def as_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["cb_ratio"] = round(self.cb_ratio, 6)
        d["cb_ratio_amount"] = round(self.cb_ratio_amount, 6)
        d["fraud_ratio"] = round(self.fraud_ratio, 6)
        return d


@dataclass
class Finding:
    merchant_id: str
    threshold: str
    metric: str
    value: float
    ceiling: float
    level: str          # "warning" | "breach"
    headroom: float     # ceiling - value (negative == over)
    note: str = ""

    def as_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["value"] = round(self.value, 6)
        d["headroom"] = round(self.headroom, 6)
        return d


@dataclass
class Report:
    windows: List[MerchantWindow] = field(default_factory=list)
    findings: List[Finding] = field(default_factory=list)
    window_days: Optional[int] = None
    records_total: int = 0
    records_used: int = 0

    @property
    def breaches(self) -> List[Finding]:
        return [f for f in self.findings if f.level == "breach"]

    @property
    def warnings(self) -> List[Finding]:
        return [f for f in self.findings if f.level == "warning"]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "window_days": self.window_days,
            "records_total": self.records_total,
            "records_used": self.records_used,
            "merchants": [w.as_dict() for w in self.windows],
            "findings": [f.as_dict() for f in self.findings],
            "summary": {
                "merchants": len(self.windows),
                "warnings": len(self.warnings),
                "breaches": len(self.breaches),
            },
        }


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _to_float(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace("$", "").replace(",", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _parse_date(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    s = str(value).strip()
    # Accept trailing Z
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    fmts = ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d", "%m/%d/%Y")
    for fmt in fmts:
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    # Last resort: fromisoformat
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def load_records(text: str, fmt: str = "auto") -> List[Dict[str, Any]]:
    """Parse feed text into a list of normalized dict records.

    fmt: "csv", "json", or "auto" (sniff by leading non-space char).
    """
    if fmt == "auto":
        stripped = text.lstrip()
        fmt = "json" if stripped[:1] in ("[", "{") else "csv"

    rows: List[Dict[str, Any]]
    if fmt == "json":
        data = json.loads(text)
        if isinstance(data, dict):
            # allow {"records": [...]} wrapper
            data = data.get("records", data.get("data", []))
        if not isinstance(data, list):
            raise ValueError("JSON feed must be a list (or object with 'records').")
        rows = [dict(r) for r in data]
    elif fmt == "csv":
        reader = csv.DictReader(io.StringIO(text))
        rows = [dict(r) for r in reader]
    else:
        raise ValueError(f"unknown format: {fmt!r}")

    norm: List[Dict[str, Any]] = []
    for r in rows:
        lowered = {str(k).strip().lower(): v for k, v in r.items() if k is not None}
        mid = lowered.get("merchant_id") or lowered.get("merchant") or lowered.get("mid")
        if not mid:
            continue
        norm.append({
            "merchant_id": str(mid).strip(),
            "type": str(lowered.get("type", "")).strip().lower(),
            "amount": _to_float(lowered.get("amount")),
            "date": _parse_date(lowered.get("date") or lowered.get("timestamp")),
            "fraud": str(lowered.get("fraud", "")).strip().lower() in ("1", "true", "yes", "y"),
        })
    return norm


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def _within_window(records: Sequence[Dict[str, Any]], window_days: Optional[int]):
    if not window_days:
        return list(records)

    def _pd(s):                                # dates arrive as ISO strings
        try:
            return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
        except (TypeError, ValueError):
            return None

    dated = [d for d in (_pd(r["date"]) for r in records if r.get("date")) if d]
    if not dated:
        return list(records)
    cutoff = max(dated) - timedelta(days=window_days)
    out = []
    for r in records:
        d = _pd(r.get("date"))
        if d is None or d >= cutoff:
            out.append(r)
    return out


def analyze_records(
    records: Sequence[Dict[str, Any]],
    thresholds: Optional[Sequence[Threshold]] = None,
    window_days: Optional[int] = None,
) -> Report:
    """Aggregate records per merchant and evaluate thresholds."""
    if thresholds is None:
        thresholds = DEFAULT_THRESHOLDS

    used = _within_window(records, window_days)
    windows: Dict[str, MerchantWindow] = {}

    for r in used:
        mid = r["merchant_id"]
        w = windows.setdefault(mid, MerchantWindow(merchant_id=mid))
        rtype = r["type"]
        try:                                   # feeds carry amounts as strings (CSV/JSON)
            amt = float(r.get("amount") or 0)
        except (TypeError, ValueError):
            amt = 0.0
        if rtype in _SETTLED_TYPES:
            w.settled_count += 1
            w.settled_amount += amt
        if rtype in _CHARGEBACK_TYPES:
            w.chargeback_count += 1
            w.chargeback_amount += amt
        if rtype in _FRAUD_TYPES or r.get("fraud"):
            w.fraud_count += 1
            w.fraud_amount += amt

    ordered = sorted(windows.values(), key=lambda x: x.cb_ratio, reverse=True)

    findings: List[Finding] = []
    for w in ordered:
        if w.settled_count == 0:
            continue
        for t in thresholds:
            value = w.metric(t.metric)
            if value >= t.ceiling:
                findings.append(Finding(
                    merchant_id=w.merchant_id,
                    threshold=t.name,
                    metric=t.metric,
                    value=value,
                    ceiling=t.ceiling,
                    level=t.level,
                    headroom=t.ceiling - value,
                    note=t.note,
                ))

    # breaches first, then warnings; within each, worst headroom first
    findings.sort(key=lambda f: (0 if f.level == "breach" else 1, f.headroom))

    return Report(
        windows=ordered,
        findings=findings,
        window_days=window_days,
        records_total=len(records),
        records_used=len(used),
    )


def analyze_file(
    path: str,
    thresholds: Optional[Sequence[Threshold]] = None,
    window_days: Optional[int] = None,
    fmt: str = "auto",
) -> Report:
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    records = load_records(text, fmt=fmt)
    return analyze_records(records, thresholds=thresholds, window_days=window_days)
