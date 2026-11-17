from typing import Annotated, Any, Optional

from pydantic import Field

from ....tool_decorator import Tool
from ....handler_wrappers import get_col
from ._media_helpers import (
    _get_file_bytes,
    _load_config,
    _store_media_item,
    _validate_media_item_inputs,
)

__all__ = ["_get_file_bytes", "_load_config", "store_media_file"]


@Tool(
    "store_media_file",
    "Store a media file to Anki's collection.media folder. Accepts files via "
    "base64 data, local file path, or URL. Use this to add images, audio, or "
    "other media files that can be referenced in note fields. "
    "Returns the stored filename and full path. "
    "See https://docs.ankiweb.net/media.html for how Anki stores and references "
    "media. URL fetches to private-network addresses are blocked unless the host "
    "is listed in the media_allowed_hosts config.",
    title="Store Media File",
    write=True,
    refresh_ui=False,
    destructive_hint=False,
    open_world_hint=True,
)
def store_media_file(
    filename: Annotated[str, Field(description="Filename to store the media under")],
    data: Annotated[Optional[str], Field(description="Base64-encoded file content; mutually exclusive with path/url")] = None,
    path: Annotated[Optional[str], Field(description="Local file path to read from; mutually exclusive with data/url")] = None,
    url: Annotated[Optional[str], Field(description="URL to download the file from; mutually exclusive with data/path")] = None,
) -> dict[str, Any]:
    col = get_col()
    sanitized_filename = _validate_media_item_inputs(filename, data, path, url)
    config = _load_config()
    media_dir = col.media.dir()

    stored = _store_media_item(
        col,
        media_dir,
        config,
        filename=filename,
        data=data,
        path=path,
        url=url,
        sanitized_filename=sanitized_filename,
    )

    return {
        **stored,
        "message": f'Successfully stored media file "{stored["filename"]}" ({stored["size"]} bytes)',
    }
