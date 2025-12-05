"""Delete media file tool - MCP tool and handler in one file.

Permanently deletes a media file from Anki's media folder.
This action cannot be undone unless you have a backup.

Thread Safety:
    - Tool handler runs in background thread
    - Actual file deletion happens on main thread via queue bridge
"""

from typing import Any, Callable, Coroutine
import logging
import os

from ....handler_registry import register_handler

logger = logging.getLogger(__name__)


# ============================================================================
# HANDLER - Runs on Qt main thread, accesses mw.col
# ============================================================================

def _delete_media_file_handler(filename: str) -> dict[str, Any]:
    """
    Delete a media file from Anki's media folder.

    This function runs on the Qt MAIN THREAD and has direct access to mw.col.
    Permanently removes the specified media file from the collection's media folder.
    This action cannot be undone unless you have a backup.

    Args:
        filename: Name of the media file to delete (e.g., "image.png", "audio.mp3")

    Returns:
        dict: Deletion result with structure:
            - success (bool): Whether operation succeeded
            - filename (str): Name of the file that was deleted
            - path (str): Full path to the deleted file
            - message (str): Human-readable result message
            - warning (str, optional): Warning about permanent deletion
            - hint (str, optional): Suggestion for next steps

    Raises:
        RuntimeError: If collection is not loaded
        ValueError: If filename validation fails
        FileNotFoundError: If file does not exist
        PermissionError: If file cannot be deleted due to permissions
    """
    from aqt import mw

    # Check if collection is loaded
    if mw.col is None:
        raise RuntimeError("Collection not loaded")

    # Security validation - prevent path traversal attacks
    if not filename or not filename.strip():
        raise ValueError("Filename cannot be empty")

    # Check for path separators to prevent directory traversal
    if os.path.sep in filename or (os.path.altsep and os.path.altsep in filename):
        raise ValueError(
            f"Filename cannot contain path separators. "
            f"Got: {filename}. Use only the filename without directory paths."
        )

    # Check for relative path indicators
    if ".." in filename or filename.startswith("."):
        raise ValueError(
            f"Filename cannot contain relative path indicators (./ or ../). "
            f"Got: {filename}"
        )

    # Get media directory
    media_dir = mw.col.media.dir()
    file_path = os.path.join(media_dir, filename)

    # Verify file exists before attempting deletion
    if not os.path.exists(file_path):
        raise FileNotFoundError(
            f"Media file not found: {filename}. "
            f"The file may have already been deleted or never existed."
        )

    # Verify it's actually a file (not a directory)
    if not os.path.isfile(file_path):
        raise ValueError(
            f"Path exists but is not a file: {filename}. "
            f"Cannot delete directories."
        )

    # WRITE operation - wrap with edit session
    # This ensures Anki's UI is refreshed after changes
    try:
        mw.requireReset()

        # Delete the file
        os.remove(file_path)

        # Trigger UI refresh if collection is still available
        if mw.col:
            mw.maybeReset()

    except PermissionError as e:
        # Ensure UI refresh even on error
        if mw.col:
            mw.maybeReset()
        raise PermissionError(
            f"Permission denied when trying to delete {filename}. "
            f"The file may be in use by another process."
        ) from e
    except Exception as e:
        # Ensure UI refresh even on error
        if mw.col:
            mw.maybeReset()
        raise

    logger.info(f"Successfully deleted media file: {filename}")

    return {
        "success": True,
        "filename": filename,
        "path": file_path,
        "message": f"Successfully deleted media file: {filename}",
        "warning": "This file has been permanently deleted from the media folder",
        "hint": "Consider syncing with AnkiWeb to propagate deletion to other devices"
    }


# Register handler at import time
register_handler("delete_media_file", _delete_media_file_handler)


# ============================================================================
# MCP TOOL - Runs in background thread, bridges to handler via queue
# ============================================================================

def register_delete_media_file_tool(
    mcp,  # FastMCP instance
    call_main_thread: Callable[[str, dict], Coroutine[Any, Any, Any]]
) -> None:
    """Register delete_media_file tool with the MCP server."""

    @mcp.tool(
        description=(
            "Delete a media file from Anki's media folder. This will permanently remove "
            "the file from disk. This action cannot be undone unless you have a backup. "
            "CRITICAL: This is destructive and permanent - only delete files the user "
            "explicitly confirmed for deletion."
        )
    )
    async def delete_media_file(filename: str) -> dict[str, Any]:
        """Delete a media file from Anki's media folder.

        This will permanently remove the specified file from the collection's media folder.
        This action cannot be undone unless you have a backup.

        Args:
            filename: Name of the media file to delete (e.g., "image.png", "audio.mp3").
                Must be just the filename without any directory paths. Path separators
                and relative path indicators (./ or ../) are not allowed for security.

        Returns:
            Dictionary containing:
            - success: Boolean indicating if the operation succeeded
            - filename: Name of the file that was deleted
            - path: Full path to the deleted file
            - message: Human-readable result message
            - warning: Warning about permanent deletion
            - hint: Suggestion for next steps
            - error: Error message (if failed)

        Raises:
            ValueError: If filename validation fails
            FileNotFoundError: If file does not exist
            PermissionError: If file cannot be deleted
            RuntimeError: If collection is not loaded

        Examples:
            >>> # Delete a media file
            >>> await delete_media_file(filename="unused_image.png")
            {
                'success': True,
                'filename': 'unused_image.png',
                'path': '/path/to/media/unused_image.png',
                'message': 'Successfully deleted media file: unused_image.png',
                'warning': 'This file has been permanently deleted from the media folder',
                'hint': 'Consider syncing with AnkiWeb to propagate deletion to other devices'
            }

            >>> # Attempting to delete with invalid path
            >>> await delete_media_file(filename="../../../etc/passwd")
            # Raises ValueError: Filename cannot contain path separators

            >>> # Attempting to delete non-existent file
            >>> await delete_media_file(filename="nonexistent.png")
            # Raises FileNotFoundError: Media file not found

        Security Features:
            - Validates filename contains no path separators
            - Prevents directory traversal attacks (../)
            - Verifies file exists before deletion
            - Ensures target is a file, not a directory
            - Returns detailed error messages for troubleshooting

        Important Notes:
            - This operation only deletes the file from disk
            - If the file is still referenced in notes, those references will become broken
            - Consider checking note references before deletion
            - The file cannot be recovered unless you have a backup
            - Sync after deletion to propagate changes to other devices
        """
        try:
            # Call main thread to execute the deletion via handler
            result = await call_main_thread("delete_media_file", {
                "filename": filename
            })

            # Return the result from handler
            return result

        except ValueError as e:
            # Handle validation errors
            error_msg = str(e)
            return {
                "success": False,
                "filename": filename,
                "error": error_msg,
                "hint": "Ensure the filename is valid and does not contain path separators or relative path indicators"
            }

        except FileNotFoundError as e:
            # Handle file not found
            error_msg = str(e)
            return {
                "success": False,
                "filename": filename,
                "error": error_msg,
                "hint": "Check that the file exists in the media folder. The file may have already been deleted."
            }

        except PermissionError as e:
            # Handle permission errors
            error_msg = str(e)
            return {
                "success": False,
                "filename": filename,
                "error": error_msg,
                "hint": "The file may be in use by Anki or another application. Try closing any windows that might be using it."
            }

        except RuntimeError as e:
            # Handle collection not loaded
            error_msg = str(e)
            if "collection not loaded" in error_msg.lower():
                return {
                    "success": False,
                    "filename": filename,
                    "error": error_msg,
                    "hint": "Make sure Anki is running and a profile is loaded"
                }

            # Other runtime errors
            return {
                "success": False,
                "filename": filename,
                "error": error_msg,
                "hint": "An unexpected error occurred. Check the Anki logs for details."
            }

        except Exception as e:
            # Handle other errors
            error_msg = str(e)
            logger.error(f"Unexpected error deleting media file {filename}: {e}", exc_info=True)
            return {
                "success": False,
                "filename": filename,
                "error": f"Unexpected error: {error_msg}",
                "hint": "An unexpected error occurred. Check the Anki logs for details."
            }
