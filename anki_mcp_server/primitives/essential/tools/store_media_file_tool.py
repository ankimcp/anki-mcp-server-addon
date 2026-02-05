from typing import Any, Optional
import base64
import urllib.request
import urllib.error
from pathlib import Path

from ....tool_decorator import Tool
from ....handler_wrappers import HandlerError, get_col


def _get_file_bytes(
    data: Optional[str],
    path: Optional[str],
    url: Optional[str],
    filename: str,
) -> tuple[bytes, str]:
    """Get file bytes from one of three sources.

    Returns:
        Tuple of (file_bytes, source_type)
    """
    if data is not None:
        try:
            return base64.b64decode(data), "base64"
        except Exception as e:
            raise HandlerError(
                f"Failed to decode base64 data: {e}",
                hint="Ensure the data is valid base64-encoded content",
                filename=filename,
            )

    if path is not None:
        file_path = Path(path)

        if not file_path.exists():
            raise HandlerError(f"File not found: {path}", filename=filename)

        if not file_path.is_file():
            raise HandlerError(f"Path is not a file: {path}", filename=filename)

        try:
            return file_path.read_bytes(), "file"
        except Exception as e:
            raise HandlerError(
                f"Failed to read file: {e}",
                filename=filename,
                path=path,
            )

    if url is not None:
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                return response.read(), "url"
        except urllib.error.HTTPError as e:
            raise HandlerError(
                f"HTTP error downloading file: {e.code} {e.reason}",
                filename=filename,
                url=url,
            )
        except urllib.error.URLError as e:
            raise HandlerError(
                f"Failed to download file: {e.reason}",
                filename=filename,
                url=url,
            )
        except Exception as e:
            raise HandlerError(
                f"Failed to download file: {e}",
                filename=filename,
                url=url,
            )

    raise HandlerError("No data source provided")


@Tool(
    "store_media_file",
    "Store a media file to Anki's collection.media folder. Accepts files via "
    "base64 data, local file path, or URL. Use this to add images, audio, or "
    "other media files that can be referenced in note fields. "
    "Returns the stored filename and full path.",
    write=True,
)
def store_media_file(
    filename: str,
    data: Optional[str] = None,
    path: Optional[str] = None,
    url: Optional[str] = None,
) -> dict[str, Any]:
    col = get_col()

    sources_provided = sum(x is not None for x in [data, path, url])
    if sources_provided == 0:
        raise HandlerError(
            "Must provide exactly one of: data, path, or url",
            hint="Specify the file source using one of the three options",
        )
    if sources_provided > 1:
        raise HandlerError(
            "Must provide exactly one of: data, path, or url (got multiple)",
            hint="Only one source can be used at a time",
        )

    if not filename or not filename.strip():
        raise HandlerError("Filename cannot be empty")

    filename = filename.strip()

    file_bytes, source_type = _get_file_bytes(data, path, url, filename)

    if not file_bytes:
        raise HandlerError("File data is empty", filename=filename)

    actual_filename = col.media.write_data(filename, file_bytes)
    media_dir = col.media.dir()
    full_path = str(Path(media_dir) / actual_filename)
    file_size = len(file_bytes)

    return {
        "filename": actual_filename,
        "filepath": full_path,
        "size": file_size,
        "message": f'Successfully stored media file "{actual_filename}" ({file_size} bytes)',
        "details": {
            "source_type": source_type,
            "original_filename": filename,
            "actual_filename": actual_filename,
        },
    }
