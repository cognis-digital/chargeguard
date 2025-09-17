"""CHARGEGUARD MCP server — exposes scan() as an MCP tool for Cognis.Studio."""
from __future__ import annotations
from chargeguard.core import scan, to_json

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
    def chargeguard_scan(target: str) -> str:
        """Monitors dispute/chargeback feeds, flags fraud-rate threshold breaches (VAMP/Visa), and drafts representment evidence packets.. Returns JSON findings."""
        return to_json(scan(target))

    app.run()
    return 0
