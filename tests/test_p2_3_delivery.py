"""P2.3 delivery follow-up.

Each tool call stores its own event. Delivery updates that id only.
A revocation that finishes after the final check and before the HTTP write
is reflected in the body that is sent. Send failures are not marked sent.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import sqlite3
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "server"))

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import BaseModel

from zhiwo.api.channel import ChannelApp, handoff
from zhiwo.api.errors import ApiError
from zhiwo.repositories.migrate import migrate
from zhiwo.services.agent_tools import search_memory
from zhiwo.services.agents import create_agent, note_client_observed, update_agent
from zhiwo.services.access import note_delivery, record_prepared
from zhiwo.services.commit_gate import commit_lock

RESULT_PATH = REPO / "tests" / "results" / "p2_3_delivery_windows.json"
SENTENCE = "交付探针：橙色封套在最终检查之后被收回。"
OTHER = "交付探针：另一条仍可返回的短句。"
REQUEST_KEY = "11111111-1111-4111-8111-111111111111"


class SearchBody(BaseModel):
    query: str
    request_id: str | None = None


def _call(func):
    try:
        return func(), None
    except ApiError as exc:
        return None, exc


def _events(db_path) -> list[sqlite3.Row]:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        return list(connection.execute("SELECT * FROM access_events ORDER BY rowid"))
    finally:
        connection.close()


def _grant(db_path, agent_id: str) -> None:
    update_agent(
        db_path,
        agent_id,
        str(uuid.uuid4()),
        {"allowed_tools": ["search_memory"], "allowed_categories": ["preference"]},
    )


def _insert(db_path, content: str, kernel_id: str) -> str:
    memory_id = str(uuid.uuid4())
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            """
            INSERT INTO memory_refs (
                memory_id, revision, kernel_id, kernel_session, kind, category, scope,
                lifecycle, share_enabled, valid_until, source_refs, approved_evidence,
                operation_id, created_at
            ) VALUES (?, 1, ?, ?, 'fact', 'preference', NULL, 'active', 1, NULL, '[]', NULL, NULL, ?)
            """,
            (memory_id, kernel_id, f"zhiwo:{memory_id}:r1", "2026-09-25T00:00:00+00:00"),
        )
        connection.commit()
    finally:
        connection.close()
    return memory_id


def _unshare(db_path, memory_id: str) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("UPDATE memory_refs SET share_enabled = 0 WHERE memory_id = ?", (memory_id,))
        connection.commit()
    finally:
        connection.close()


def _status(db_path, agent_id: str) -> str:
    connection = sqlite3.connect(db_path)
    try:
        return connection.execute("SELECT client_status FROM agents WHERE id = ?", (agent_id,)).fetchone()[0]
    finally:
        connection.close()


def test_delivery_followup() -> None:
    if sys.platform != "win32" or platform.system() != "Windows" or os.environ.get("WSL_DISTRO_NAME"):
        raise AssertionError("P2.3 delivery follow-up requires native Windows")
    result: dict = {"task": "P2.3-delivery", "platform": platform.platform(), "a12": "NOT_RUN"}
    secrets: list[str] = []
    try:
        with tempfile.TemporaryDirectory(prefix="zhiwo-p23d-", ignore_cleanup_errors=True) as raw:
            db = Path(raw) / "zhiwo.db"
            result["schema_version"] = migrate(db)
            first = create_agent(db, str(uuid.uuid4()), "合成连接甲")
            second = create_agent(db, str(uuid.uuid4()), "合成连接乙")
            secrets.extend([first["credential"], second["credential"]])
            _grant(db, first["id"])
            _grant(db, second["id"])
            kept = _insert(db, SENTENCE, "kernel-kept")
            _insert(db, OTHER, "kernel-other")

            def recall(query, _top_k):
                if query == SENTENCE:
                    return [{"id": "kernel-kept", "content": SENTENCE}]
                if query == OTHER:
                    return [{"id": "kernel-other", "content": OTHER}]
                return []

            left = search_memory(db, None, first["credential"], SENTENCE, request_id=REQUEST_KEY, recall=recall)
            right = search_memory(db, None, second["credential"], SENTENCE, request_id=REQUEST_KEY, recall=recall)
            rows = _events(db)
            note_delivery(db, rows[0]["id"], "sent")
            after_one = _events(db)
            retry = search_memory(db, None, first["credential"], OTHER, request_id=REQUEST_KEY, recall=recall)
            rows = _events(db)
            note_delivery(db, rows[2]["id"], "failed")
            after_retry = _events(db)
            conflict = None
            try:
                note_delivery(db, rows[2]["id"], "sent")
            except ApiError as exc:
                conflict = exc.status
            stayed = _events(db)[2]["delivery_state"]

            def fail_commit(connection, spec):
                record_id = record_prepared(connection, spec)

                def boom():
                    raise sqlite3.OperationalError("disk I/O error")

                connection.commit = boom
                return record_id

            before_fail = len(_events(db))
            failed_body, audit = _call(
                lambda: search_memory(
                    db,
                    None,
                    first["credential"],
                    SENTENCE,
                    recall=recall,
                    record=fail_commit,
                )
            )
            after_fail = len(_events(db))

            prepared = search_memory(db, None, first["credential"], SENTENCE, recall=recall)
            prepared_id = _events(db)[-1]["id"]
            prepared_snapshot = _events(db)[-1]["response_snapshot"]

            async def interrupt(message):
                if message["type"] == "http.response.body":
                    raise ConnectionError("interrupted")

            interrupt_error = None
            try:
                asyncio.run(handoff(db, prepared_id, interrupt))
            except ConnectionError as exc:
                interrupt_error = type(exc).__name__
            interrupted = _events(db)[-1]

            uncertain_call = search_memory(db, None, first["credential"], OTHER, recall=recall)
            uncertain_id = _events(db)[-1]["id"]
            uncertain_snapshot = _events(db)[-1]["response_snapshot"]

            async def ambiguous(message):
                if message["type"] == "http.response.body":
                    raise RuntimeError("channel result unknown")

            uncertain_error = None
            try:
                asyncio.run(handoff(db, uncertain_id, ambiguous))
            except RuntimeError as exc:
                uncertain_error = type(exc).__name__
            uncertain_row = _events(db)[-1]

            started = threading.Event()
            release = threading.Event()
            ordered = search_memory(db, None, second["credential"], OTHER, recall=recall)
            ordered_id = _events(db)[-1]["id"]
            sent_messages: list[dict] = []

            async def blocking_send(message):
                sent_messages.append(message)
                if message["type"] == "http.response.body":
                    started.set()
                    while not release.is_set():
                        await asyncio.sleep(0.01)

            holder: dict = {}

            def run_handoff():
                try:
                    asyncio.run(handoff(db, ordered_id, blocking_send))
                except Exception as exc:
                    holder["error"] = f"{type(exc).__name__}: {exc}"

            sender = threading.Thread(target=run_handoff)
            sender.start()
            started.wait(5)
            revoker_finished = threading.Event()

            def revoke_other():
                with commit_lock:
                    _unshare(db, _memory_id(db, "kernel-other"))
                revoker_finished.set()

            revoker = threading.Thread(target=revoke_other)
            revoker.start()
            revoker.join(0.4)
            revoke_waited = revoker.is_alive()
            release.set()
            sender.join(5)
            revoker.join(5)
            ordered_body = json.loads(sent_messages[-1]["body"])
            followed = search_memory(db, None, second["credential"], OTHER, recall=recall)
            followed_messages: list[dict] = []

            async def collect(message):
                followed_messages.append(message)

            asyncio.run(handoff(db, _events(db)[-1]["id"], collect))
            followed_body = json.loads(followed_messages[-1]["body"])

            api = FastAPI()
            api.state.settings = SimpleNamespace(control_db=db)
            api.state.before_channel = None
            seen: dict = {}

            @api.post("/api/v1/agent/tools/search_memory")
            def route(request: Request, body: SearchBody) -> dict:
                return search_memory(
                    db,
                    None,
                    request.headers.get("x-test-credential") or "",
                    body.query,
                    request_id=body.request_id,
                    recall=recall,
                )

            app = ChannelApp(api)
            with TestClient(app) as client:
                plain = client.post(
                    "/api/v1/agent/tools/search_memory",
                    headers={"x-test-credential": first["credential"]},
                    json={"query": SENTENCE, "request_id": str(uuid.uuid4())},
                )
                plain_row = _events(db)[-1]
                verified_before = _status(db, first["id"])

                def revoke_kept():
                    seen["prepared_had_sentence"] = SENTENCE in _events(db)[-1]["response_snapshot"]
                    _unshare(db, kept)

                api.state.before_channel = revoke_kept
                revised = client.post(
                    "/api/v1/agent/tools/search_memory",
                    headers={"x-test-credential": first["credential"]},
                    json={"query": SENTENCE, "request_id": str(uuid.uuid4())},
                )
                api.state.before_channel = None
                revised_row = _events(db)[-1]
                stdio = client.post(
                    "/api/v1/agent/tools/search_memory",
                    headers={
                        "x-test-credential": second["credential"],
                        "X-Zhiwo-Transport": "stdio",
                    },
                    json={"query": "没有这条记忆", "request_id": str(uuid.uuid4())},
                )
                verified_after = _status(db, second["id"])

            disabled = create_agent(db, str(uuid.uuid4()), "合成连接丙")
            secrets.append(disabled["credential"])
            _grant(db, disabled["id"])
            update_agent(db, disabled["id"], str(uuid.uuid4()), {"enabled": False})
            note_client_observed(db, disabled["id"])
            disabled_status = _status(db, disabled["id"])

            result.update(
                {
                    "distinct_events": rows[0]["id"] != rows[1]["id"] and rows[1]["id"] != rows[2]["id"],
                    "same_request_id": rows[0]["request_id"] == rows[1]["request_id"] == REQUEST_KEY,
                    "different_agents": rows[0]["agent_id"] != rows[1]["agent_id"],
                    "one_notice_changes_one_row": after_one[0]["delivery_state"] == "sent" and after_one[1]["delivery_state"] == "prepared",
                    "other_snapshot_unchanged": after_one[1]["response_snapshot"] == rows[1]["response_snapshot"],
                    "retry_event": retry["request_id"] == REQUEST_KEY and after_retry[2]["delivery_state"] == "failed",
                    "retry_leaves_previous": after_retry[0]["delivery_state"] == "sent" and after_retry[1]["delivery_state"] == "prepared",
                    "illegal_transition": conflict == 409 and stayed == "failed",
                    "audit_status": None if audit is None else audit.status,
                    "audit_code": None if audit is None else audit.code,
                    "audit_hides_text": audit is not None and SENTENCE not in audit.message and failed_body is None,
                    "audit_not_stored": after_fail == before_fail,
                    "interrupt_error": interrupt_error,
                    "interrupt_state": interrupted["delivery_state"],
                    "interrupt_snapshot_unchanged": interrupted["response_snapshot"] == prepared_snapshot,
                    "interrupt_not_sent": interrupted["delivery_state"] != "sent",
                    "uncertain_error": uncertain_error,
                    "uncertain_state": uncertain_row["delivery_state"],
                    "uncertain_snapshot_unchanged": uncertain_row["response_snapshot"] == uncertain_snapshot,
                    "uncertain_not_sent": uncertain_row["delivery_state"] != "sent",
                    "ordered_waited": started.is_set() and revoke_waited and revoker_finished.is_set() and "error" not in holder,
                    "ordered_body_kept_checked_item": OTHER in json.dumps(ordered_body, ensure_ascii=False),
                    "ordered_next_hides": followed["items"] == [] and OTHER not in json.dumps(followed_body, ensure_ascii=False),
                    "http_status": plain.status_code,
                    "http_body_matches_snapshot": plain.json() == json.loads(plain_row["response_snapshot"]),
                    "http_delivery": plain_row["delivery_state"],
                    "http_event_absent_from_body": "event_id" not in plain.json(),
                    "pending_without_stdio": verified_before == "pending",
                    "checked_before_channel": seen.get("prepared_had_sentence") is True,
                    "revised_status": revised.status_code,
                    "revised_hides": SENTENCE not in revised.text,
                    "revised_matches": revised.json() == json.loads(revised_row["response_snapshot"]),
                    "revised_delivery": revised_row["delivery_state"],
                    "stdio_empty_status": stdio.status_code,
                    "verified_after_stdio": verified_after == "verified",
                    "disabled_stays_pending": disabled_status == "pending",
                    "payloads_are_dicts": isinstance(left, dict) and isinstance(right, dict) and isinstance(prepared, dict) and isinstance(uncertain_call, dict) and isinstance(ordered, dict),
                    "model_read_claimed": "已采用" in revised.text or "已阅读" in revised.text,
                }
            )
        blob = json.dumps({key: value for key, value in result.items() if key != "credential_in_result"}, ensure_ascii=False)
        result["credential_in_result"] = any(secret and secret in blob for secret in secrets)
        result["pass"] = all(
            [
                result.get("schema_version") == 8,
                result.get("distinct_events") is True and result.get("same_request_id") is True,
                result.get("different_agents") is True and result.get("one_notice_changes_one_row") is True,
                result.get("other_snapshot_unchanged") is True and result.get("retry_leaves_previous") is True,
                result.get("retry_event") is True and result.get("illegal_transition") is True,
                result.get("audit_status") == 503 and result.get("audit_code") == "AUDIT_UNAVAILABLE",
                result.get("audit_hides_text") is True and result.get("audit_not_stored") is True,
                result.get("interrupt_error") == "ConnectionError" and result.get("interrupt_state") == "failed",
                result.get("interrupt_not_sent") is True and result.get("interrupt_snapshot_unchanged") is True,
                result.get("uncertain_error") == "RuntimeError" and result.get("uncertain_state") == "unknown",
                result.get("uncertain_not_sent") is True and result.get("uncertain_snapshot_unchanged") is True,
                result.get("ordered_waited") is True and result.get("ordered_body_kept_checked_item") is True,
                result.get("ordered_next_hides") is True,
                result.get("http_status") == 200 and result.get("http_body_matches_snapshot") is True,
                result.get("http_delivery") == "sent" and result.get("http_event_absent_from_body") is True,
                result.get("pending_without_stdio") is True and result.get("checked_before_channel") is True,
                result.get("revised_status") == 200 and result.get("revised_hides") is True,
                result.get("revised_matches") is True and result.get("revised_delivery") == "sent",
                result.get("stdio_empty_status") == 200 and result.get("verified_after_stdio") is True,
                result.get("disabled_stays_pending") is True and result.get("model_read_claimed") is False,
                result.get("credential_in_result") is False and result.get("payloads_are_dicts") is True,
            ]
        )
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["pass"] = False
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if not result["pass"]:
        raise AssertionError(json.dumps(result, ensure_ascii=False, indent=2))


def _memory_id(db_path, kernel_id: str) -> str:
    connection = sqlite3.connect(db_path)
    try:
        return connection.execute(
            "SELECT memory_id FROM memory_refs WHERE kernel_id = ?",
            (kernel_id,),
        ).fetchone()[0]
    finally:
        connection.close()


if __name__ == "__main__":
    test_delivery_followup()
