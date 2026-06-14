"""CHARGEGUARD MCP server — exposes scan() as an MCP tool for Cognis.Studio."""
from __future__ import annotations
import json
from chargeguard.core import analyze_file


def serve() -> int:
    """Start an MCP stdio server. Requires the optional 'mcp' extra:
        pip install "cognis-chargeguard[mcp]"
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except Exception:
        print("Install the MCP extra: pip install 'cognis-chargeguard[mcp]'")
        return 1
    app = FastMCP("chargeguard")

    @app.tool()
    def chargeguard_scan(feed_path: str) -> str:
        """Scan a chargeback/transaction feed (CSV or JSON) for ratio breaches.

        Returns JSON with merchant aggregates and threshold findings.
        """
        try:
            report = analyze_file(feed_path)
        except (FileNotFoundError, PermissionError, ValueError, OSError) as exc:
            return json.dumps({"error": str(exc)})
        return json.dumps(report.as_dict(), indent=2)

    app.run()
    return 0
