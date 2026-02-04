from typing import Any, Optional
import os
import fnmatch

from anki_mcp_server.tool_decorator import Tool
from anki_mcp_server.handler_wrappers import HandlerError, get_col


@Tool(
    "get_media_files_names",
    "List all media files in Anki's media folder with optional pattern filtering. "
    "Use patterns like '*.mp3' for audio files, '*.jpg' for images, etc.",
)
def get_media_files_names(pattern: Optional[str] = None) -> dict[str, Any]:
    col = get_col()
    media_dir = col.media.dir()

    if not os.path.exists(media_dir):
        return {
            "message": f"Media directory does not exist: {media_dir}",
            "files": [],
            "total": 0,
            "media_folder": media_dir,
        }

    try:
        all_entries = os.listdir(media_dir)
    except OSError as e:
        raise HandlerError(f"Failed to list media directory: {e}")

    all_files = [
        entry for entry in all_entries
        if os.path.isfile(os.path.join(media_dir, entry))
    ]

    if pattern:
        filtered_files = [
            filename for filename in all_files
            if fnmatch.fnmatch(filename, pattern)
        ]

        result: dict[str, Any] = {
            "files": filtered_files,
            "total": len(filtered_files),
            "pattern": pattern,
            "media_folder": media_dir,
        }

        if not filtered_files:
            result["message"] = f"No files found matching pattern: {pattern}"

        return result

    result = {
        "files": all_files,
        "total": len(all_files),
        "media_folder": media_dir,
    }

    if not all_files:
        result["message"] = "No files found in media folder"

    return result
