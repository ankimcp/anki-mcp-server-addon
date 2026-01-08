from typing import Any
import os

from ....tool_decorator import Tool, ToolError, get_col


@Tool(
    "delete_media_file",
    "Delete a media file from Anki's media folder. This will permanently remove "
    "the file from disk. This action cannot be undone unless you have a backup. "
    "CRITICAL: This is destructive and permanent - only delete files the user "
    "explicitly confirmed for deletion.",
    write=True,
)
def delete_media_file(filename: str) -> dict[str, Any]:
    col = get_col()

    if not filename or not filename.strip():
        raise ToolError("Filename cannot be empty")

    if os.path.sep in filename or (os.path.altsep and os.path.altsep in filename):
        raise ToolError(
            f"Filename cannot contain path separators. Got: {filename}",
            hint="Use only the filename without directory paths",
        )

    if ".." in filename or filename.startswith("."):
        raise ToolError(
            f"Filename cannot contain relative path indicators (./ or ../). Got: {filename}",
            hint="Use only the filename without relative path components",
        )

    media_dir = col.media.dir()
    file_path = os.path.join(media_dir, filename)

    if not os.path.exists(file_path):
        raise ToolError(
            f"Media file not found: {filename}",
            hint="The file may have already been deleted or never existed",
        )

    if not os.path.isfile(file_path):
        raise ToolError(
            f"Path exists but is not a file: {filename}",
            hint="Cannot delete directories",
        )

    try:
        os.remove(file_path)
    except PermissionError:
        raise ToolError(
            f"Permission denied when trying to delete {filename}",
            hint="The file may be in use by another process",
        )

    return {
        "filename": filename,
        "path": file_path,
        "message": f"Successfully deleted media file: {filename}",
        "warning": "This file has been permanently deleted from the media folder",
        "hint": "Consider syncing with AnkiWeb to propagate deletion to other devices",
    }
