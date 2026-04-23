from typing import Any
import os

from ....tool_decorator import Tool
from ....handler_wrappers import HandlerError, get_col
from ....media_validators import sanitize_media_filename


@Tool(
    "delete_media_file",
    "Move a media file to Anki's trash folder. The file can be recovered via "
    "Anki's 'Check Media' dialog until the trash is emptied. Sync to propagate "
    "the deletion to other devices. Confirm with the user before deleting.",
    write=True,
)
def delete_media_file(filename: str) -> dict[str, Any]:
    col = get_col()

    if not filename or not filename.strip():
        raise HandlerError("Filename cannot be empty")

    filename = sanitize_media_filename(filename)

    media_dir = col.media.dir()
    file_path = os.path.join(media_dir, filename)

    if not os.path.exists(file_path):
        raise HandlerError(
            f"Media file not found: {filename}",
            hint="The file may have already been deleted or never existed",
        )

    if not os.path.isfile(file_path):
        raise HandlerError(
            f"Path exists but is not a file: {filename}",
            hint="Cannot delete directories",
        )

    col.media.trash_files([filename])

    return {
        "filename": filename,
        "path": file_path,
        "message": f"Successfully moved media file to trash: {filename}",
        "hint": "File can be recovered via Anki's 'Check Media' dialog. Sync to propagate deletion to other devices.",
    }
