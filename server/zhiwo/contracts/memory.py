"""Control record for one published memory version.

`kind` is fact or event. `category` is the permission category.
`scope` is the scenario and is not a category. The body stays in Mnemosyne.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Kind = Literal["fact", "event"]
Category = Literal["identity", "goal", "preference", "project", "event", "other"]
Lifecycle = Literal["active", "superseded", "deleting"]


class MemoryRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    kernel_id: str | None = None
    kernel_session: str = Field(min_length=1)
    kind: Kind
    category: Category
    scope: str | None = None
    lifecycle: Lifecycle
    share_enabled: bool = False
    valid_until: str | None = None
    source_refs: str | None = None
    approved_evidence: str | None = None
    operation_id: str | None = None
