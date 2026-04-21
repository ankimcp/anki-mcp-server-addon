"""Register static asset HTTP routes (favicon, etc.) on the FastMCP server."""

from pathlib import Path
from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import Response

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


def _load_favicon() -> bytes | None:
    favicon_path = Path(__file__).parent / "static" / "favicon.ico"
    try:
        return favicon_path.read_bytes()
    except OSError as exc:
        print(f"AnkiMCP Server: failed to load favicon at {favicon_path}: {exc}")
        return None


_FAVICON_BYTES: bytes | None = _load_favicon()


def register_static_routes(mcp: "FastMCP") -> None:
    """Register static asset routes on the given FastMCP server."""
    if _FAVICON_BYTES is None:
        return

    favicon_bytes = _FAVICON_BYTES

    @mcp.custom_route("/favicon.ico", methods=["GET"])
    async def favicon(request: Request) -> Response:
        return Response(
            content=favicon_bytes,
            media_type="image/x-icon",
            headers={"Cache-Control": "public, max-age=86400"},
        )
