"""Store media file tool - MCP tool and handler in one file."""
from typing import Any, Callable, Coroutine, Optional
import logging
import base64
import urllib.request
import urllib.error
from pathlib import Path

from ....handler_registry import register_handler

logger = logging.getLogger(__name__)


# ============================================================================
# HANDLER - Runs on Qt main thread, accesses mw.col
# ============================================================================

def _store_media_file_handler(
    filename: str,
    data: Optional[str] = None,
    path: Optional[str] = None,
    url: Optional[str] = None,
    delete_existing: bool = True,
) -> dict[str, Any]:
    """
    Store a media file to Anki's collection.media folder.

    This function runs on the Qt MAIN THREAD and has direct access to mw.col.

    Args:
        filename: Name of the file to store (e.g., "image.png", "audio.mp3")
        data: Base64-encoded file data (mutually exclusive with path and url)
        path: Local file path to read and store (mutually exclusive with data and url)
        url: URL to download file from (mutually exclusive with data and path)
        delete_existing: If True, replace existing file with same name. Default True.

    Returns:
        dict: Result with structure:
            - success (bool): Whether the operation succeeded
            - filename (str): The stored filename
            - filepath (str): Full path to stored file
            - size (int): Size in bytes
            - message (str): Human-readable result message
            - error (str, optional): Error message if operation failed

    Raises:
        RuntimeError: If collection is not loaded
        ValueError: If parameter validation fails

    Note:
        Exactly one of data, path, or url must be provided.
        For URLs, this function will download the file synchronously on the main thread.
        For large files, this may cause UI freezing - use responsibly.
    """
    from aqt import mw

    # Check if collection is loaded
    if mw.col is None:
        raise RuntimeError("Collection not loaded")

    # Validate that exactly one source is provided
    sources_provided = sum(x is not None for x in [data, path, url])
    if sources_provided == 0:
        raise ValueError("Must provide exactly one of: data, path, or url")
    if sources_provided > 1:
        raise ValueError("Must provide exactly one of: data, path, or url (got multiple)")

    # Validate filename is not empty
    if not filename or not filename.strip():
        return {
            "success": False,
            "error": "Filename cannot be empty",
        }

    filename = filename.strip()

    # Get file data as bytes based on source
    file_bytes: bytes
    source_type: str

    try:
        if data is not None:
            # Decode base64 data
            source_type = "base64"
            try:
                file_bytes = base64.b64decode(data)
            except Exception as e:
                return {
                    "success": False,
                    "error": f"Failed to decode base64 data: {str(e)}",
                    "filename": filename,
                }

        elif path is not None:
            # Read from file path
            source_type = "file"
            file_path = Path(path)

            if not file_path.exists():
                return {
                    "success": False,
                    "error": f"File not found: {path}",
                    "filename": filename,
                }

            if not file_path.is_file():
                return {
                    "success": False,
                    "error": f"Path is not a file: {path}",
                    "filename": filename,
                }

            try:
                file_bytes = file_path.read_bytes()
            except Exception as e:
                return {
                    "success": False,
                    "error": f"Failed to read file: {str(e)}",
                    "filename": filename,
                    "path": path,
                }

        elif url is not None:
            # Download from URL
            source_type = "url"
            try:
                with urllib.request.urlopen(url, timeout=30) as response:
                    file_bytes = response.read()
            except urllib.error.HTTPError as e:
                return {
                    "success": False,
                    "error": f"HTTP error downloading file: {e.code} {e.reason}",
                    "filename": filename,
                    "url": url,
                }
            except urllib.error.URLError as e:
                return {
                    "success": False,
                    "error": f"Failed to download file: {str(e.reason)}",
                    "filename": filename,
                    "url": url,
                }
            except Exception as e:
                return {
                    "success": False,
                    "error": f"Failed to download file: {str(e)}",
                    "filename": filename,
                    "url": url,
                }
        else:
            # This should never happen due to validation above
            raise RuntimeError("Unreachable: no data source provided")

    except Exception as e:
        logger.exception(f"Unexpected error getting file data from {source_type}")
        return {
            "success": False,
            "error": f"Unexpected error: {str(e)}",
            "filename": filename,
        }

    # Check if we got any data
    if not file_bytes:
        return {
            "success": False,
            "error": "File data is empty",
            "filename": filename,
        }

    # WRITE operation - wrap with edit session
    try:
        mw.requireReset()

        # Delete existing file if requested
        if delete_existing:
            # Check if file exists and delete it
            # Anki's media manager will handle this when we write with same name
            pass

        # Write file to media collection
        # write_data returns the actual filename used (may be different if renamed)
        actual_filename = mw.col.media.write_data(filename, file_bytes)

        # Get full path to verify
        media_dir = mw.col.media.dir()
        full_path = str(Path(media_dir) / actual_filename)

    finally:
        if mw.col:
            mw.maybeReset()

    # Return success response
    file_size = len(file_bytes)

    return {
        "success": True,
        "filename": actual_filename,
        "filepath": full_path,
        "size": file_size,
        "message": f'Successfully stored media file "{actual_filename}" ({file_size} bytes)',
        "details": {
            "source_type": source_type,
            "original_filename": filename,
            "actual_filename": actual_filename,
            "replaced_existing": delete_existing,
        },
    }


# Register handler at import time
register_handler("store_media_file", _store_media_file_handler)


# ============================================================================
# MCP TOOL - Runs in background thread, bridges to handler via queue
# ============================================================================

def register_store_media_file_tool(
    mcp,  # FastMCP instance
    call_main_thread: Callable[[str, dict], Coroutine[Any, Any, Any]]
) -> None:
    """Register store-media-file tool with the MCP server."""

    @mcp.tool(
        description=(
            "Store a media file to Anki's collection.media folder. Accepts files via "
            "base64 data, local file path, or URL. Use this to add images, audio, or "
            "other media files that can be referenced in note fields. "
            "Returns the stored filename and full path."
        )
    )
    async def store_media_file(
        filename: str,
        data: Optional[str] = None,
        path: Optional[str] = None,
        url: Optional[str] = None,
        delete_existing: bool = True,
    ) -> dict[str, Any]:
        """Store a media file to Anki's collection.media folder.

        Provides three ways to specify the file source:
        1. Base64-encoded data string
        2. Local file path
        3. URL to download from

        Exactly one of data, path, or url must be provided.

        Args:
            filename: Name to save the file as (e.g., "diagram.png", "pronunciation.mp3").
                Must include file extension. Anki may rename if a file with this name exists.
            data: Base64-encoded file data. Use for inline data or when file is already
                encoded. Mutually exclusive with path and url.
            path: Absolute path to a local file to copy to media folder.
                Mutually exclusive with data and url.
            url: HTTP(S) URL to download file from. Downloads synchronously (may take time
                for large files). Mutually exclusive with data and path.
            delete_existing: If True (default), replace any existing file with the same name.
                If False and file exists, Anki will rename the new file (e.g., "image-1.png").

        Returns:
            Dictionary containing:
            - success (bool): Whether the operation succeeded
            - filename (str): The actual filename stored (may differ from input if renamed)
            - filepath (str): Full absolute path to the stored file
            - size (int): File size in bytes
            - message (str): Human-readable result message
            - details (dict): Additional information:
                - source_type (str): "base64", "file", or "url"
                - original_filename (str): The requested filename
                - actual_filename (str): The filename actually used
                - replaced_existing (bool): Whether existing file was replaced
            - error (str): Error message (if failed)

        Raises:
            Exception: If media storage fails on the main thread

        Note:
            - Files are stored in Anki's collection.media folder
            - Anki automatically handles media syncing to AnkiWeb
            - Supported formats: images (jpg, png, gif, svg, etc.), audio (mp3, ogg, etc.),
              video (mp4, webm, etc.), and any other file type
            - For URLs, download happens synchronously and may freeze UI for large files
            - Maximum URL download timeout is 30 seconds

        Examples:
            Store from base64:
            >>> result = await store_media_file(
            ...     filename="logo.png",
            ...     data="iVBORw0KGgoAAAANSUhEUgAAAAUA..."
            ... )
            >>> print(result['filepath'])  # /path/to/anki/collection.media/logo.png

            Store from local file:
            >>> result = await store_media_file(
            ...     filename="audio.mp3",
            ...     path="/Users/me/Downloads/pronunciation.mp3"
            ... )

            Store from URL:
            >>> result = await store_media_file(
            ...     filename="diagram.jpg",
            ...     url="https://example.com/images/diagram.jpg"
            ... )

            Reference in note field:
            >>> # After storing, reference in HTML using [sound:...] or <img src="...">
            >>> await addNote(
            ...     deckName="Vocabulary",
            ...     modelName="Basic",
            ...     fields={
            ...         "Front": "What does this image show?",
            ...         "Back": '<img src="diagram.jpg">'
            ...     }
            ... )
        """
        # Prepare arguments for main thread
        arguments = {
            "filename": filename,
            "data": data,
            "path": path,
            "url": url,
            "delete_existing": delete_existing,
        }

        return await call_main_thread("store_media_file", arguments)
