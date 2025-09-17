# Demo 01 — Basic chargeback ratio scan

## What this shows

`feed.csv` is a small acquirer-style transaction feed for three merchants over
a few days. Each row is one event with a `type`:

- `sale` / `settled` — counts toward the denominator (settled transactions)
- `chargeback` — counts toward the chargeback numerator
- `fraud` — counts toward the fraud numerator
- `refund` — ignored for ratio math

CHARGEGUARD aggregates per merchant and compares each merchant's ratios to
Visa VAMP-style ceilings:

| Threshold            | Metric           | Ceiling | Level   |
|----------------------|------------------|---------|---------|
| VAMP early-warning   | dispute count %  | 0.65%   | warning |
| VAMP standard        | dispute count %  | 0.90%   | breach  |
| VAMP excessive       | dispute count %  | 1.50%   | breach  |
| Dispute $ ratio      | dispute amount % | 1.00%   | warning |
| Fraud ratio          | fraud count %    | 0.90%   | breach  |

## The merchants

- **acme_co** — 1000 settled, 12 chargebacks => 1.20% dispute ratio.
  Crosses the 0.90% standard ceiling (BREACH) and 0.65% early-warning.
- **good_shop** — 800 settled, 3 chargebacks => 0.375% dispute ratio. Clean.
- **risky_mart** — 500 settled, 9 chargebacks + heavy fraud => 1.80% dispute
  ratio (BREACH, excessive tier) and elevated fraud ratio (BREACH).

## Run it

```sh
python -m chargeguard scan demos/01-basic/feed.csv
```

Expected: a merchant table plus FINDINGS listing breaches for `acme_co` and
`risky_mart`. Because breaches are present, the process **exits 1** — so this
doubles as a CI gate.

JSON for pipelines:

```sh
python -m chargeguard scan demos/01-basic/feed.csv --format json | jq '.summary'
```

Expected summary: `breaches >= 2`, and `good_shop` produces no findings.
