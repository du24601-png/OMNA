"""Local Owner service. Listens on 127.0.0.1 and does not import experiments."""

from __future__ import annotations

from contextlib import asynccontextmanager

import uuid
from typing import Annotated

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from zhiwo.adapters.kernel_client import KernelHandle, connect
from zhiwo.api.auth import OwnerAuthError, install_redaction, owner_auth_error, require_owner
from zhiwo.api.errors import ApiError, api_error
from zhiwo.config import load_settings
from zhiwo.contracts.memory import Category, Kind
from zhiwo.repositories.migrate import memory_ref_count, migrate, schema_version, setting
from zhiwo.services.imports import import_source, retry_import
from zhiwo.services.memories import build_profile, get_memory, get_source, list_memories, update_memory
from zhiwo.services.publish import operation_status, publish_memory
from zhiwo.services.review import decide_proposal, get_proposal, list_proposals
from zhiwo.services.search import list_versions, search_memories


@asynccontextmanager
async def _lifespan(app: FastAPI):
    settings = load_settings()
    install_redaction(settings.owner_credential, settings.extractor_api_key)
    app.state.settings = settings
    app.state.schema_version = migrate(settings.control_db)
    app.state.kernel = connect(
        settings.kernel_dir,
        cache_dir=settings.fastembed_cache,
        connect_only=settings.connect_only,
    )
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="知我", lifespan=_lifespan)
    app.add_exception_handler(OwnerAuthError, owner_auth_error)
    app.add_exception_handler(ApiError, api_error)
    app.add_exception_handler(RequestValidationError, _validation_error)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/health", dependencies=[Depends(require_owner)])
    def owner_health(request: Request) -> dict:
        kernel: KernelHandle = request.app.state.kernel
        db_path = request.app.state.settings.control_db
        return {
            "status": "ok",
            "schema_version": schema_version(db_path),
            "embedding_model": setting(db_path, "embedding_model"),
            "embeddings_loaded": kernel.embeddings_loaded,
            "connect_only": kernel.connect_only,
            "extractor_configured": request.app.state.settings.extractor_configured,
            "test_mode": request.app.state.settings.test_mode,
            "memory_ref_count": memory_ref_count(db_path),
            "kernel": {
                "connected": True,
                "version": kernel.version,
                "db_path": str(kernel.db_path),
            },
        }

    @app.post("/api/v1/memories", dependencies=[Depends(require_owner)])
    def create_memory(
        request: Request,
        body: MemoryBody,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    ) -> dict:
        return publish_memory(
            request.app.state.settings.control_db,
            request.app.state.kernel,
            _request_id(idempotency_key),
            body.model_dump(),
        )

    @app.get("/api/v1/profile", dependencies=[Depends(require_owner)])
    def profile(request: Request) -> dict:
        return build_profile(request.app.state.settings.control_db, request.app.state.kernel)

    @app.get("/api/v1/memories", dependencies=[Depends(require_owner)])
    def find_memories(
        request: Request,
        query: str | None = None,
        state: str = "current",
        category: str | None = None,
        limit: int = 20,
    ) -> dict:
        db_path = request.app.state.settings.control_db
        kernel = request.app.state.kernel
        if query and query.strip() and state == "current":
            found = search_memories(db_path, kernel, query, min(max(limit, 1), 20))
            if category:
                found["items"] = [item for item in found["items"] if item["category"] == category]
            return found
        return list_memories(
            db_path,
            kernel,
            state=state,
            category=category,
            query=query,
            limit=min(max(limit, 1), 50),
        )

    @app.get("/api/v1/memories/{memory_id}/versions", dependencies=[Depends(require_owner)])
    def memory_versions(request: Request, memory_id: str) -> dict:
        return list_versions(request.app.state.settings.control_db, request.app.state.kernel, memory_id)

    @app.get("/api/v1/memories/{memory_id}", dependencies=[Depends(require_owner)])
    def memory(request: Request, memory_id: str) -> dict:
        return get_memory(request.app.state.settings.control_db, request.app.state.kernel, memory_id)

    @app.patch("/api/v1/memories/{memory_id}", dependencies=[Depends(require_owner)])
    def patch_memory(
        request: Request,
        memory_id: str,
        body: MemoryPatch,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    ) -> dict:
        return update_memory(
            request.app.state.settings.control_db,
            request.app.state.kernel,
            memory_id,
            _request_id(idempotency_key),
            body.model_dump(),
        )

    @app.get("/api/v1/sources/{source_id}", dependencies=[Depends(require_owner)])
    def source(request: Request, source_id: str) -> dict:
        return get_source(request.app.state.settings.control_db, source_id)

    @app.get("/api/v1/proposals", dependencies=[Depends(require_owner)])
    def proposals(request: Request, status: str | None = None) -> dict:
        return list_proposals(
            request.app.state.settings.control_db,
            demo=request.app.state.settings.test_mode,
            status=status,
        )

    @app.get("/api/v1/proposals/{proposal_id}", dependencies=[Depends(require_owner)])
    def proposal(request: Request, proposal_id: str) -> dict:
        return get_proposal(
            request.app.state.settings.control_db,
            proposal_id,
            demo=request.app.state.settings.test_mode,
        )

    @app.post("/api/v1/proposals/{proposal_id}/decision", dependencies=[Depends(require_owner)])
    def proposal_decision(
        request: Request,
        proposal_id: str,
        body: DecisionBody,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    ) -> dict:
        return decide_proposal(
            request.app.state.settings.control_db,
            request.app.state.kernel,
            proposal_id,
            _request_id(idempotency_key),
            body.model_dump(),
        )

    @app.get("/api/v1/operations/{operation_id}", dependencies=[Depends(require_owner)])
    def operation(request: Request, operation_id: str) -> dict:
        return operation_status(request.app.state.settings.control_db, _request_id(operation_id))

    @app.post("/api/v1/imports", dependencies=[Depends(require_owner)])
    def create_import(
        request: Request,
        body: ImportBody,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    ) -> dict:
        return import_source(
            request.app.state.settings.control_db,
            request.app.state.settings,
            _request_id(idempotency_key),
            body.model_dump(),
        )

    @app.post("/api/v1/imports/{job_id}/retry", dependencies=[Depends(require_owner)])
    def retry_job(request: Request, job_id: str) -> dict:
        return retry_import(request.app.state.settings.control_db, request.app.state.settings, _request_id(job_id))

    return app


class MemoryBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str
    kind: Kind
    category: Category
    scope: str | None = None
    valid_until: str | None = None
    share_enabled: bool = True
    source_refs: list[str] = Field(default_factory=list)


class MemoryPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str
    kind: Kind
    category: Category
    scope: str | None = None
    valid_until: str | None = None
    share_enabled: bool = True
    source_refs: list[str] = Field(default_factory=list)
    base_revision: int


class DecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str
    base_revision: int | None = None
    target_id: str | None = None
    content: str | None = None
    kind: Kind | None = None
    category: Category | None = None
    scope: str | None = None
    valid_until: str | None = None
    share_enabled: bool | None = None


class ImportBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    name: str | None = None
    text: str | None = None
    content_base64: str | None = None


def _validation_error(_request: Request, _exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "the submitted fields are invalid",
                "retryable": False,
            }
        },
    )


def _request_id(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ApiError(400, "VALIDATION_ERROR", "request id must be a UUID") from exc


app = create_app()
