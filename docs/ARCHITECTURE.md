# CHARGEGUARD — Architecture

> Monitors dispute/chargeback feeds, flags fraud-rate threshold breaches (VAMP/Visa), and drafts representment evidence packets.

```
input ──▶ collect ──▶ rules/analyzers ──▶ score ──▶ findings ──▶ table · json
                              │                          │
                         (this repo)                 MCP tool (agents)
```

- **collect** normalizes the target (file/dir/API) into records.
- **rules/analyzers** apply the heuristics shipped in `chargeguard/core.py`.
- **score** ranks by severity.
- **MCP server** (`chargeguard mcp`) exposes `scan` for Cognis.Studio agents.

Extend by adding a rule + a test + a `demos/NN-*/SCENARIO.md`. See [CONTRIBUTING.md](../CONTRIBUTING.md).
