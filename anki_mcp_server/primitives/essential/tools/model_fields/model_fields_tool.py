"""Multi-action tool for managing fields on an existing note type (model)."""
from typing import Annotated, Any, ClassVar, Literal, Optional, Union

from pydantic import BaseModel, Field

from .....tool_decorator import Tool
from .....handler_wrappers import HandlerError

from .actions.add import add_field_impl
from .actions.remove import remove_field_impl
from .actions.rename import rename_field_impl
from .actions.reposition import reposition_field_impl

_BASE_DESCRIPTION = (
    "Manage fields on an existing note type (model). Every action changes the note "
    "type schema, which forces a one-way full sync on the next sync -- this "
    "overwrites the collection on AnkiWeb and your other devices."
)


class AddParams(BaseModel):
    _tool_description: ClassVar[str] = (
        "add: Add a new field to a note type. Optionally position it with a 0-based "
        "index (default: appended at the end)."
    )
    action: Literal["add"]
    model_name: str = Field(description="Name of the note type (model) to modify")
    field_name: str = Field(description="Name of the new field to add")
    index: Optional[int] = Field(
        default=None,
        description="0-based position for the new field (0..field_count, inclusive). "
                    "Omit to append at the end.",
    )


class RemoveParams(BaseModel):
    _destructive: ClassVar[bool] = True
    _tool_description: ClassVar[str] = (
        "remove: Permanently delete a field AND its content from every note of this "
        "type. Irreversible bulk data loss -- the deleted content is recoverable only "
        "from a backup. A note type must keep at least one field."
    )
    action: Literal["remove"]
    model_name: str = Field(description="Name of the note type (model) to modify")
    field_name: str = Field(description="Name of the field to remove")


class RenameParams(BaseModel):
    _tool_description: ClassVar[str] = (
        "rename: Rename an existing field, preserving its content. Card templates are "
        "NOT updated automatically -- any reference to {{OldFieldName}} renders blank "
        "until you fix it manually with the update_model_templates tool."
    )
    action: Literal["rename"]
    model_name: str = Field(description="Name of the note type (model) to modify")
    field_name: str = Field(description="Current name of the field to rename")
    new_name: str = Field(description="New name for the field")


class RepositionParams(BaseModel):
    _tool_description: ClassVar[str] = (
        "reposition: Move an existing field to a new 0-based position, reordering the "
        "fields of the note type."
    )
    action: Literal["reposition"]
    model_name: str = Field(description="Name of the note type (model) to modify")
    field_name: str = Field(description="Name of the field to move")
    index: int = Field(
        description="0-based target position for the field (0..field_count-1)",
    )


ModelFieldsParams = Annotated[
    Union[AddParams, RemoveParams, RenameParams, RepositionParams],
    Field(discriminator="action"),
]


@Tool(
    "model_fields",
    _BASE_DESCRIPTION,  # Rebuilt dynamically at MCP registration from _tool_description ClassVars
    write=True,
)
def model_fields(params: ModelFieldsParams) -> dict[str, Any]:
    match params.action:
        case "add":
            return add_field_impl(
                model_name=params.model_name,
                field_name=params.field_name,
                index=params.index,
            )
        case "remove":
            return remove_field_impl(
                model_name=params.model_name,
                field_name=params.field_name,
            )
        case "rename":
            return rename_field_impl(
                model_name=params.model_name,
                field_name=params.field_name,
                new_name=params.new_name,
            )
        case "reposition":
            return reposition_field_impl(
                model_name=params.model_name,
                field_name=params.field_name,
                index=params.index,
            )
        case _:
            raise HandlerError(f"Unknown action: {params.action}")
