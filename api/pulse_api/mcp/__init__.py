"""HTTPS MCP server.

Wraps the existing admin REST surface as Model Context Protocol tools so
LLM clients (Claude Code, claude.ai, any MCP-capable host) can drive the
engagement lifecycle directly. Mounted on the same FastAPI app under
`/api/mcp` and authenticated with the same `Authorization: Bearer
pulse_<key>` header the REST endpoints accept.

The MCP layer holds zero business logic — every tool is a thin wrapper
over an existing `pulse_api.repos.*` helper or `pulse_api.storage`
function. The auth path reuses `_user_from_bearer` so the wire-level
behaviour stays identical between transports.
"""
from pulse_api.mcp.server import mcp_app

__all__ = ["mcp_app"]
