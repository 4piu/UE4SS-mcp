"""MCP server entry point and tool registration."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from ue4ss_mcp_server.context import resolve_context as _resolve_context

mcp = MCPServer("ue4ss-mcp")


@mcp.tool()
def resolve_context(
    game: str | None = None,
    version: str | None = None,
    log_path: str | None = None,
) -> dict:
    """Resolve which UE4SS version + game subsequent tool calls apply to.

    Pass `game`/`version` explicitly, or `log_path` to a UE4SS.log to
    detect them from its startup banner. Explicit values win over
    anything detected from the log.
    """
    ctx = _resolve_context(game=game, version=version, log_path=log_path)
    return {
        "game": ctx.game,
        "version": ctx.version,
        "source": ctx.source,
        "build_sha": ctx.build_sha,
        "channel": ctx.channel,
    }


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
