"""Store media files tool - batch-store multiple media files in Anki."""

from typing import Annotated, Any, Optional

from pydantic import BaseModel, Field

from ....config import get_max_notes_per_batch
from ....handler_wrappers import HandlerError, get_col
from ....tool_decorator import Tool
from ._media_helpers import _load_config, _store_media_item


class MediaFileEntry(BaseModel):
    """A single media file to store in a batch."""

    filename: str = Field(description="Filename to store the media under")
    data: Optional[str] = Field(
        default=None,
        description="Base64-encoded file content; mutually exclusive with path/url",
    )
    path: Optional[str] = Field(
        default=None,
        description="Local file path to read from; mutually exclusive with data/url",
    )
    url: Optional[str] = Field(
        default=None,
        description="URL to download the file from; mutually exclusive with data/path",
    )


@Tool(
    "store_media_files",
    "Store multiple media files to Anki's collection.media folder in a single batch. "
    "Each entry accepts a file via base64 data, local file path, or URL. "
    "Supports partial success - individual file failures do not affect others. "
    "Returns summary counts (stored, failed) and a per-file results array with "
    "stored filename and full path. "
    "URL fetches to private-network addresses are blocked unless the host is "
    "listed in the media_allowed_hosts config.",
    title="Store Media Files",
    write=True,
    refresh_ui=False,
    destructive_hint=False,
    open_world_hint=True,
)
def store_media_files(
    files: Annotated[
        list[MediaFileEntry],
        Field(description='Media files to store; each {"filename": ..., and one of "data", "path", or "url"}'),
    ],
) -> dict[str, Any]:
    col = get_col()
    max_files = get_max_notes_per_batch()

    if not files:
        raise HandlerError(
            "Files array is empty",
            hint="Provide at least one file in the files array.",
            code="validation_error",
        )

    if len(files) > max_files:
        raise HandlerError(
            f"Too many files: {len(files)} (maximum is {max_files})",
            hint=f"Split your request into batches of {max_files} or fewer. "
                 f"You can increase the limit via the 'max_notes_per_batch' addon config option.",
            code="limit_exceeded",
            requested=len(files),
            maximum=max_files,
        )

    config = _load_config()
    media_dir = col.media.dir()

    results: list[dict[str, Any]] = []
    for i, raw_entry in enumerate(files):
        raw_filename = (
            raw_entry.get("filename", "")
            if isinstance(raw_entry, dict)
            else raw_entry.filename
        )
        try:
            entry = (
                MediaFileEntry.model_validate(raw_entry)
                if isinstance(raw_entry, dict)
                else raw_entry
            )
            stored = _store_media_item(
                col,
                media_dir,
                config,
                filename=entry.filename,
                data=entry.data,
                path=entry.path,
                url=entry.url,
            )
            results.append({"index": i, "status": "stored", **stored})
        except HandlerError as e:
            failed_entry: dict[str, Any] = {
                "index": i,
                "status": "failed",
                "filename": raw_filename,
                "error": e.message,
            }
            if e.hint:
                failed_entry["hint"] = e.hint
            results.append(failed_entry)
        except Exception as e:
            results.append({
                "index": i,
                "status": "failed",
                "filename": raw_filename,
                "error": str(e),
            })

    stored_count = sum(1 for r in results if r["status"] == "stored")
    failed_count = sum(1 for r in results if r["status"] == "failed")
    total = len(files)
    detail = f" ({failed_count} failed)" if failed_count else ""

    return {
        "stored": stored_count,
        "failed": failed_count,
        "total_requested": total,
        "max_files_per_batch": max_files,
        "results": results,
        "message": f"Stored {stored_count} of {total} media files{detail}",
    }
