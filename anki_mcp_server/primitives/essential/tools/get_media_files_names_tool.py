"""Get media files names tool - MCP tool and handler in one file."""
from typing import Any, Callable, Coroutine
import logging
import os
import fnmatch

from ....handler_registry import register_handler

logger = logging.getLogger(__name__)


# ============================================================================
# HANDLER - Runs on Qt main thread, accesses mw.col
# ============================================================================

def _get_media_files_names_handler(pattern: str | None = None) -> dict[str, Any]:
    """
    List all files in Anki's media folder with optional filtering.

    This function runs on the Qt MAIN THREAD and has direct access to mw.col.

    Args:
        pattern: Optional glob pattern to filter files (e.g., "*.mp3", "*.jpg", "image_*.png").
                If None, returns all files in the media folder.

    Returns:
        dict: Response with structure:
            - success (bool): Always True for successful operations
            - files (list[str]): List of filenames in the media folder
            - total (int): Total number of files returned
            - pattern (str, optional): The pattern used for filtering, if provided
            - media_folder (str): Absolute path to the media folder
            - message (str, optional): Info message if no files found

    Raises:
        RuntimeError: If collection is not loaded

    Note:
        - Only returns actual files, not directories
        - Files are returned in the order provided by the filesystem
        - The pattern uses shell-style wildcards (*, ?, [seq], [!seq])
        - Pattern matching is case-sensitive on Unix-like systems
    """
    from aqt import mw

    if mw.col is None:
        raise RuntimeError("Collection not loaded")

    # Get the media folder path
    media_dir = mw.col.media.dir()

    if not os.path.exists(media_dir):
        logger.warning(f"Media directory does not exist: {media_dir}")
        return {
            "success": True,
            "message": f"Media directory does not exist: {media_dir}",
            "files": [],
            "total": 0,
            "media_folder": media_dir,
        }

    # List all entries in the media folder
    try:
        all_entries = os.listdir(media_dir)
    except OSError as e:
        logger.error(f"Failed to list media directory: {e}")
        raise RuntimeError(f"Failed to list media directory: {e}")

    # Filter to only include files (not directories)
    all_files = [
        entry for entry in all_entries
        if os.path.isfile(os.path.join(media_dir, entry))
    ]

    # Apply pattern filter if provided
    if pattern:
        filtered_files = [
            filename for filename in all_files
            if fnmatch.fnmatch(filename, pattern)
        ]

        result = {
            "success": True,
            "files": filtered_files,
            "total": len(filtered_files),
            "pattern": pattern,
            "media_folder": media_dir,
        }

        if not filtered_files:
            result["message"] = f"No files found matching pattern: {pattern}"

        return result
    else:
        # Return all files
        result = {
            "success": True,
            "files": all_files,
            "total": len(all_files),
            "media_folder": media_dir,
        }

        if not all_files:
            result["message"] = "No files found in media folder"

        return result


# Register handler at import time
register_handler("get_media_files_names", _get_media_files_names_handler)


# ============================================================================
# MCP TOOL - Runs in background thread, bridges to handler via queue
# ============================================================================

def register_get_media_files_names_tool(
    mcp,
    call_main_thread: Callable[[str, dict], Coroutine[Any, Any, Any]]
) -> None:
    """Register the get_media_files_names MCP tool."""

    @mcp.tool(
        description="List all media files in Anki's media folder with optional pattern filtering. "
                   "Use patterns like '*.mp3' for audio files, '*.jpg' for images, etc."
    )
    async def get_media_files_names(pattern: str | None = None) -> dict[str, Any]:
        """List all media files in Anki's media folder with optional filtering.

        Returns a list of all files stored in Anki's media collection folder.
        This includes images, audio files, videos, and any other media referenced
        by notes. You can optionally filter the results using shell-style glob patterns.

        Args:
            pattern: Optional glob pattern to filter files. Examples:
                - "*.mp3" - all MP3 audio files
                - "*.jpg" - all JPG images
                - "audio_*.mp3" - MP3 files starting with "audio_"
                - "image[0-9].png" - image files like image1.png, image2.png, etc.
                If not provided, returns all files.

        Returns:
            Dictionary containing:
            - success (bool): Always True for successful operations
            - files (list[str]): List of filenames matching the criteria
            - total (int): Number of files returned
            - pattern (str, optional): The pattern used for filtering, if provided
            - media_folder (str): Absolute path to Anki's media folder
            - message (str, optional): Informational message if no files found

        Raises:
            Exception: If the collection is not loaded or media folder is inaccessible

        Example:
            >>> # Get all media files
            >>> result = await get_media_files_names()
            >>> print(result['files'])  # ['photo.jpg', 'audio.mp3', 'video.mp4']
            >>>
            >>> # Get only MP3 files
            >>> result = await get_media_files_names(pattern="*.mp3")
            >>> print(result['files'])  # ['audio.mp3', 'pronunciation.mp3']
            >>>
            >>> # Get images starting with 'diagram_'
            >>> result = await get_media_files_names(pattern="diagram_*.png")
            >>> print(result['total'])  # Number of matching files

        Note:
            - Only actual files are returned, not directories
            - The pattern uses Unix shell-style wildcards:
              * matches everything
              ? matches any single character
              [seq] matches any character in seq
              [!seq] matches any character not in seq
            - Pattern matching is case-sensitive on Unix-like systems
            - Files are returned in filesystem order (not sorted)
            - This accesses the collection on the main thread
        """
        return await call_main_thread("get_media_files_names", {"pattern": pattern})
