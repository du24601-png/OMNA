"""P2.2 follow-up and P2.3 access snapshots.

The Owner decision check goes through the HTTP route. A real MCP client
stays P2.4. A12 is not retried here.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sqlite3
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "server"))

from fastapi.testclient import TestClient

from zhiwo.adapters.kernel_client import connect
from zhiwo.api.app import create_app
from zhiwo.api.errors import ApiError
from zhiwo.config import load_settings
from zhiwo.repositories.migrate import migrate
from zhiwo.services.access import note_delivery, record_prepared
from zhiwo.services.agent_tools import propose_memory, search_memory
from zhiwo.services.agents import AgentPrincipal, create_agent, rotate_credential, update_agent
from zhiwo.services.policy import memory_visible, select_visible
from zhiwo.services.publish import publish_memory
from zhiwo.services.review import decide_proposal

RESULT_PATH = REPO / "tests" / "results" / "p2_3_windows.json"
CACHE = REPO / "experiments" / "kernel_spike" / "runs" / "p0_3" / "fastembed-cache"
UNTIL = "2099-06-01T00:00:00+00:00"
PRIVATE = "收尾探针：蓝色手册只在周日使用。"
PRIVATE_NEXT = "收尾探针：蓝色手册只在周日上午使用。"
SHARED = "收尾探针：橙色文件夹放在第二层。"
SHARED_NEXT = "收尾探针：橙色文件夹已经换了句子。"
SHORT = "收尾探针：跳过后仍可返回的短句。"
HIDDEN = "收尾探针：缺生命周期的句子不能出现。"


def _source(db_path, content: str) -> str:
    source_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    now = "2026-09-25T00:00:00+00:00"
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            """
            INSERT INTO sources (id, kind, name, content, content_hash, imported_at)
            VALUES (?, 'paste', NULL, ?, ?, ?)
            """,
            (source_id, content, digest, now),
        )
        connection.execute(
            """
            INSERT INTO import_jobs (id, source_id, status, extractor_config, error_code, created_at)
            VALUES (?, ?, 'extracted', NULL, NULL, ?)
            """,
            (job_id, source_id, now),
        )
        connection.commit()
    finally:
        connection.close()
    return source_id


def _proposal(db_path, content: str, source_id: str) -> str:
    proposal_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    payload = {"content": content, "kind": "fact", "category": "preference", "scope": None, "share_enabled": True}
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            """
            INSERT INTO proposals (
                id, origin, change_type, payload_json, evidence_json, status,
                source_id, job_id, created_at
            ) VALUES (?, 'import', 'add', ?, ?, 'pending', ?, ?, ?)
            """,
            (
                proposal_id,
                json.dumps(payload, ensure_ascii=False),
                json.dumps({"text": "片段"}, ensure_ascii=False),
                source_id,
                job_id,
                "2026-09-25T00:00:00+00:00",
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return proposal_id


def _active(db_path, memory_id: str):
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(
            """
            SELECT revision, share_enabled, valid_until
            FROM memory_refs
            WHERE memory_id = ? AND lifecycle = 'active'
            """,
            (memory_id,),
        ).fetchone()
    finally:
        connection.close()


def _intent(db_path, proposal_id: str) -> dict:
    connection = sqlite3.connect(db_path)
    try:
        raw = connection.execute("SELECT intent_json FROM proposals WHERE id = ?", (proposal_id,)).fetchone()[0]
    finally:
        connection.close()
    return json.loads(raw)


def _events(db_path) -> list:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute("SELECT * FROM access_events ORDER BY created_at, id").fetchall()
    finally:
        connection.close()


def _call(func):
    try:
        return func(), None
    except ApiError as exc:
        return None, exc


def _grant(db_path, agent_id: str, tools: list[str], categories: list[str]) -> None:
    update_agent(db_path, agent_id, str(uuid.uuid4()), {"allowed_tools": tools, "allowed_categories": categories})


def _prepare_env(raw: str, owner: str) -> None:
    os.environ["ZHIWO_DATA_DIR"] = raw
    os.environ["ZHIWO_OWNER_CREDENTIAL"] = owner
    os.environ["ZHIWO_FASTEMBED_CACHE_DIR"] = str(CACHE)
    os.environ["ZHIWO_HOST"] = "127.0.0.1"
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ.pop("ZHIWO_KERNEL_CONNECT_ONLY", None)
    os.environ.pop("ZHIWO_EXTRACTOR_API_KEY", None)
    os.environ.pop("ZHIWO_EXTRACTOR_BASE_URL", None)
    os.environ.pop("ZHIWO_EXTRACTOR_MODEL", None)


def _owner_api(owner: str) -> dict:
    with TestClient(create_app()) as client:
        auth = {"Authorization": f"Bearer {owner}"}
        created = client.post(
            "/api/v1/memories",
            headers={**auth, "Idempotency-Key": str(uuid.uuid4())},
            json={
                "content": PRIVATE,
                "kind": "fact",
                "category": "preference",
                "share_enabled": False,
                "valid_until": UNTIL,
            },
        )
        created_body = created.json()
        db = Path(os.environ["ZHIWO_DATA_DIR"]) / "zhiwo.db"
        source_id = _source(db, "接口来源。")
        proposal_id = _proposal(db, PRIVATE_NEXT, source_id)
        omitted = {
            "decision": "update",
            "target_id": created_body["memory_id"],
            "base_revision": created_body["revision"],
            "content": PRIVATE_NEXT,
        }
        first = client.post(
            f"/api/v1/proposals/{proposal_id}/decision",
            headers={**auth, "Idempotency-Key": str(uuid.uuid4())},
            json=omitted,
        )
        stored = _intent(db, proposal_id)
        replay = client.post(
            f"/api/v1/proposals/{proposal_id}/decision",
            headers={**auth, "Idempotency-Key": str(uuid.uuid4())},
            json=omitted,
        )
        changed = client.post(
            f"/api/v1/proposals/{proposal_id}/decision",
            headers={**auth, "Idempotency-Key": str(uuid.uuid4())},
            json={**omitted, "share_enabled": True, "valid_until": None},
        )
        provided = client.post(
            f"/api/v1/proposals/{proposal_id}/decision",
            headers={**auth, "Idempotency-Key": str(uuid.uuid4())},
            json={**omitted, "share_enabled": False},
        )
        current = client.get(f"/api/v1/memories/{created_body['memory_id']}", headers=auth)
        memory = current.json()
        agent = client.post(
            "/api/v1/agents",
            headers={**auth, "Idempotency-Key": str(uuid.uuid4())},
            json={"name": "接口连接"},
        ).json()
        client.patch(
            f"/api/v1/agents/{agent['id']}",
            headers={**auth, "Idempotency-Key": str(uuid.uuid4())},
            json={"allowed_tools": ["search_memory"], "allowed_categories": ["preference"]},
        )
        shared = client.post(
            "/api/v1/memories",
            headers={**auth, "Idempotency-Key": str(uuid.uuid4())},
            json={"content": SHARED, "kind": "fact", "category": "preference", "share_enabled": True},
        )
        searched = client.post(
            "/api/v1/agent/tools/search_memory",
            headers={"Authorization": f"Bearer {agent['credential']}"},
            json={"query": SHARED},
        )
        rows = _events(db)
        matched = [row for row in rows if row["request_id"] == searched.json().get("request_id")]
        snapshot = json.loads(matched[0]["response_snapshot"]) if matched else None
        listed = client.get(f"/api/v1/access-events?agent_id={agent['id']}", headers=auth)
        detail = None
        if listed.status_code == 200 and listed.json()["events"]:
            detail = client.get(f"/api/v1/access-events/{listed.json()['events'][0]['id']}", headers=auth).json()
        return {
            "created_status": created.status_code,
            "omitted_status": first.status_code,
            "omitted_share": memory.get("share_enabled"),
            "omitted_until": memory.get("valid_until"),
            "intent_share_provided": stored.get("share_provided"),
            "intent_until_provided": stored.get("valid_until_provided"),
            "replay_status": replay.status_code,
            "replay_revision": None if replay.status_code != 200 else replay.json().get("revision"),
            "first_revision": None if first.status_code != 200 else first.json().get("revision"),
            "clear_status": changed.status_code,
            "provided_false_status": provided.status_code,
            "client_status": agent.get("client_status"),
            "connected_label_in_agent": "已连接" in json.dumps(agent, ensure_ascii=False),
            "http_search_status": searched.status_code,
            "http_body_matches_snapshot": snapshot == searched.json(),
            "http_delivery": None if not matched else matched[0]["delivery_state"],
            "http_hit": SHARED in json.dumps(searched.json(), ensure_ascii=False),
            "list_has_response_body": any("response" in event for event in listed.json().get("events", [])),
            "detail_matches_snapshot": None if detail is None else detail.get("response") == snapshot,
            "agent_secret": agent.get("credential"),
        }


def test_access_and_followup() -> None:
    if sys.platform != "win32" or platform.system() != "Windows" or os.environ.get("WSL_DISTRO_NAME"):
        raise AssertionError("P2.3 requires native Windows")
    if not CACHE.exists():
        raise AssertionError(f"local embedding cache is missing: {CACHE}")
    owner = "p23-owner-" + uuid.uuid4().hex
    secrets = [owner]
    result: dict = {
        "task": "P2.3",
        "platform": platform.platform(),
        "python": platform.python_version(),
        "real_client": "NOT_RUN",
        "a12": "NOT_RUN",
        "stage_complete": False,
    }
    try:
        with tempfile.TemporaryDirectory(prefix="zhiwo-p23-", ignore_cleanup_errors=True) as raw:
            _prepare_env(raw, owner)
            settings = load_settings()
            result["schema_version"] = migrate(settings.control_db)
            kernel = connect(settings.kernel_dir, cache_dir=settings.fastembed_cache, connect_only=False)
            db = settings.control_db
            source_id = _source(db, "合成来源。")

            def save(content: str, *, share: bool, until: str | None = None, target: str | None = None, revision: int | None = None) -> dict:
                payload = {
                    "content": content,
                    "kind": "fact",
                    "category": "preference",
                    "share_enabled": share,
                    "valid_until": until,
                    "source_refs": [],
                }
                if target is not None:
                    payload["target_id"] = target
                    payload["base_revision"] = revision
                return publish_memory(db, kernel, str(uuid.uuid4()), payload)

            private = save(PRIVATE, share=False, until=UNTIL)
            proposal_id = _proposal(db, PRIVATE_NEXT, source_id)
            omitted = {
                "decision": "update",
                "content": PRIVATE_NEXT,
                "target_id": private["memory_id"],
                "base_revision": private["revision"],
            }
            inherited = decide_proposal(db, kernel, proposal_id, str(uuid.uuid4()), omitted)
            inherited_row = _active(db, private["memory_id"])
            replayed = decide_proposal(db, kernel, proposal_id, str(uuid.uuid4()), omitted)
            _, conflict = _call(
                lambda: decide_proposal(
                    db,
                    kernel,
                    proposal_id,
                    str(uuid.uuid4()),
                    {**omitted, "share_enabled": False},
                )
            )
            after_conflict = _active(db, private["memory_id"])

            agent = create_agent(db, str(uuid.uuid4()), "合成连接甲")
            other = create_agent(db, str(uuid.uuid4()), "合成连接乙")
            secrets.extend([agent["credential"], other["credential"]])
            _grant(db, agent["id"], ["search_memory", "propose_memory"], ["preference"])
            _grant(db, other["id"], ["search_memory", "propose_memory"], ["preference"])
            request_key = str(uuid.uuid4())
            change = {"type": "add", "content": "提案探针：上午喝茶。", "kind": "fact", "category": "preference"}
            evidence = {"text": "提案探针。"}
            first = propose_memory(db, kernel, agent["credential"], request_key, change, evidence)
            second = propose_memory(db, kernel, other["credential"], request_key, change, evidence)
            connection = sqlite3.connect(db)
            try:
                connection.execute("UPDATE proposals SET status = 'accepted' WHERE id = ?", (first["proposal_id"],))
                connection.commit()
            finally:
                connection.close()
            again = propose_memory(db, kernel, agent["credential"], request_key, change, evidence)
            other_again = propose_memory(db, kernel, other["credential"], request_key, change, evidence)
            _, payload_conflict = _call(
                lambda: propose_memory(
                    db,
                    kernel,
                    agent["credential"],
                    request_key,
                    {**change, "content": "提案探针：改成了别的句子。"},
                    evidence,
                )
            )

            now = datetime.now(timezone.utc)
            principal = AgentPrincipal("a", "n", True, 1, ("search_memory",), ("preference",))
            visible = {
                "memory_id": "m-short",
                "revision": 1,
                "kind": "fact",
                "category": "preference",
                "scope": None,
                "lifecycle": "active",
                "share_enabled": 1,
                "valid_until": None,
            }
            missing = dict(visible)
            missing["memory_id"] = "m-missing"
            missing["lifecycle"] = None
            selected = select_visible(
                [
                    {"id": "long", "content": "甲" * 2001},
                    {"id": "missing", "content": HIDDEN},
                    {"id": "short", "content": SHORT},
                ],
                {"long": {**visible, "memory_id": "m-long"}, "missing": missing, "short": visible},
                principal,
                limit=10,
                now=now,
            )

            shared = save(SHARED, share=True)
            follower = save(SHORT, share=True)
            before_events = len(_events(db))
            seen = {}

            def recording(connection, spec):
                event_id = record_prepared(connection, spec)
                other_connection = sqlite3.connect(db)
                try:
                    seen["during"] = other_connection.execute("SELECT COUNT(*) FROM access_events").fetchone()[0]
                finally:
                    other_connection.close()
                seen["payload"] = spec["payload"]
                return event_id

            committed = search_memory(
                db,
                kernel,
                agent["credential"],
                SHARED,
                recall=lambda _query, _top_k: [{"id": shared["kernel_id"], "content": SHARED}],
                record=recording,
            )
            after_commit = len(_events(db))
            stored_snapshot = json.loads(_events(db)[-1]["response_snapshot"])
            prepared_state = _events(db)[-1]["delivery_state"]
            prepared_id = _events(db)[-1]["id"]
            note_delivery(db, prepared_id, "failed")
            failed_state = _events(db)[-1]["delivery_state"]
            failed_snapshot = _events(db)[-1]["response_snapshot"]
            replace_failed = None
            try:
                note_delivery(db, prepared_id, "sent")
            except ApiError as exc:
                replace_failed = exc.status
            still_failed = _events(db)[-1]["delivery_state"]
            seen["replace_failed"] = replace_failed

            skipped = search_memory(
                db,
                kernel,
                agent["credential"],
                SHORT,
                recall=lambda _query, _top_k: [
                    {"id": shared["kernel_id"], "content": "甲" * 2001},
                    {"id": follower["kernel_id"], "content": SHORT},
                ],
            )

            def unshare():
                save(SHARED, share=False, target=shared["memory_id"], revision=shared["revision"])

            inflight, inflight_error = _call(
                lambda: search_memory(
                    db,
                    kernel,
                    agent["credential"],
                    SHARED,
                    before_commit=unshare,
                    recall=lambda _query, _top_k: [{"id": shared["kernel_id"], "content": SHARED}],
                )
            )
            revoked = search_memory(
                db,
                kernel,
                agent["credential"],
                SHARED,
                recall=lambda _query, _top_k: [
                    {"id": shared["kernel_id"], "content": SHARED},
                    {"id": shared["kernel_id"], "content": SHARED_NEXT},
                ],
            )
            revoked_snapshot = json.loads(_events(db)[-1]["response_snapshot"])

            version_before = agent["policy_version"]
            # The grant above incremented policy. Read the live row.
            live = sqlite3.connect(db)
            try:
                version_before = live.execute("SELECT policy_version FROM agents WHERE id = ?", (agent["id"],)).fetchone()[0]
            finally:
                live.close()

            def rotate():
                rotate_credential(db, agent["id"], str(uuid.uuid4()))

            _, rotate_error = _call(
                lambda: search_memory(
                    db,
                    kernel,
                    agent["credential"],
                    SHARED,
                    before_commit=rotate,
                    recall=lambda _query, _top_k: [{"id": follower["kernel_id"], "content": SHORT}],
                )
            )
            live = sqlite3.connect(db)
            try:
                version_after = live.execute("SELECT policy_version, enabled FROM agents WHERE id = ?", (agent["id"],)).fetchone()
            finally:
                live.close()
            disabled = update_agent(db, other["id"], str(uuid.uuid4()), {"enabled": False})
            rotated_disabled = rotate_credential(db, other["id"], str(uuid.uuid4()))
            secrets.append(rotated_disabled.get("credential") or "")

            def fail_audit(_connection, _spec):
                raise sqlite3.OperationalError("disk")

            third = create_agent(db, str(uuid.uuid4()), "合成连接丙")
            secrets.append(third["credential"])
            _grant(db, third["id"], ["search_memory"], ["preference"])
            before_fail = len(_events(db))
            _, audit_error = _call(
                lambda: search_memory(
                    db,
                    kernel,
                    third["credential"],
                    SHORT,
                    recall=lambda _query, _top_k: [{"id": follower["kernel_id"], "content": SHORT}],
                    record=fail_audit,
                )
            )
            after_fail = len(_events(db))

            result.update(
                {
                    "inherited_share": bool(inherited_row["share_enabled"]),
                    "inherited_until": inherited_row["valid_until"],
                    "replay_revision": replayed.get("revision"),
                    "inherited_revision": inherited.get("revision"),
                    "intent_conflict": None if conflict is None else conflict.status,
                    "conflict_revision": after_conflict["revision"],
                    "separate_proposals": first["proposal_id"] != second["proposal_id"],
                    "other_status": second["status"],
                    "other_hides_first": first["proposal_id"] not in json.dumps(second, ensure_ascii=False),
                    "same_agent_replay_status": again["status"],
                    "same_agent_replay_id": again["proposal_id"] == first["proposal_id"],
                    "other_stays_pending": other_again["status"] == "pending" and other_again["proposal_id"] == second["proposal_id"],
                    "payload_conflict": None if payload_conflict is None else payload_conflict.status,
                    "long_skipped": [item["id"] for item in selected["items"]] == ["m-short"],
                    "long_truncated": selected["truncated"] is True,
                    "missing_lifecycle_hidden": memory_visible(principal, missing, now) is False and HIDDEN not in json.dumps(selected, ensure_ascii=False),
                    "tool_skips_long": [item["content"] for item in skipped["items"]] == [SHORT] and skipped["truncated"] is True,
                    "snapshot_uncommitted_during_write": seen.get("during") == before_events,
                    "snapshot_visible_after_return": after_commit == before_events + 1,
                    "snapshot_is_payload": stored_snapshot == committed and committed is seen.get("payload"),
                    "prepared_state": prepared_state,
                    "failed_state": failed_state,
                    "failed_snapshot_unchanged": failed_snapshot == json.dumps(committed, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    "sent_does_not_replace_failed": still_failed == "failed",
                    "inflight_error": None if inflight_error is None else inflight_error.status,
                    "inflight_items": None if inflight is None else inflight.get("items"),
                    "revoked_items": revoked["items"],
                    "revoked_snapshot_matches": revoked_snapshot == revoked,
                    "revoked_hides_old": SHARED not in json.dumps(revoked, ensure_ascii=False) and SHARED not in json.dumps(revoked_snapshot, ensure_ascii=False),
                    "rotate_status": None if rotate_error is None else rotate_error.status,
                    "rotate_message": None if rotate_error is None else rotate_error.message,
                    "rotate_version_delta": None if version_after is None else version_after[0] - version_before,
                    "rotate_text_absent": rotate_error is not None and SHORT not in rotate_error.message,
                    "disabled_rotate_enabled": rotated_disabled.get("enabled"),
                    "disabled_rotate_version": rotated_disabled.get("policy_version"),
                    "disable_version": disabled.get("policy_version"),
                    "audit_status": None if audit_error is None else audit_error.status,
                    "audit_code": None if audit_error is None else audit_error.code,
                    "audit_hides_text": audit_error is not None and SHORT not in audit_error.message,
                    "audit_not_stored": after_fail == before_fail,
                    "model_read_claimed": "已采用" in failed_snapshot or "已阅读" in failed_snapshot,
                }
            )

        http_owner = "p23-http-" + uuid.uuid4().hex
        secrets.append(http_owner)
        with tempfile.TemporaryDirectory(prefix="zhiwo-p23-http-", ignore_cleanup_errors=True) as raw:
            _prepare_env(raw, http_owner)
            http = _owner_api(http_owner)
            secrets.append(http.pop("agent_secret") or "")
            result["http"] = http
        blob = json.dumps(result, ensure_ascii=False)
        result["credential_in_result"] = any(secret and secret in blob for secret in secrets)
        result["pass"] = all(
            [
                result.get("schema_version") == 8,
                result.get("inherited_share") is False and result.get("inherited_until") == UNTIL,
                result.get("replay_revision") == result.get("inherited_revision"),
                result.get("intent_conflict") == 409 and result.get("conflict_revision") == result.get("inherited_revision"),
                result.get("separate_proposals") is True and result.get("other_status") == "pending" and result.get("other_hides_first") is True,
                result.get("same_agent_replay_status") == "accepted" and result.get("same_agent_replay_id") is True,
                result.get("other_stays_pending") is True and result.get("payload_conflict") == 409,
                result.get("long_skipped") is True and result.get("long_truncated") is True and result.get("missing_lifecycle_hidden") is True,
                result.get("tool_skips_long") is True,
                result.get("snapshot_uncommitted_during_write") is True and result.get("snapshot_visible_after_return") is True,
                result.get("snapshot_is_payload") is True and result.get("prepared_state") == "prepared",
                result.get("failed_state") == "failed" and result.get("failed_snapshot_unchanged") is True,
                result.get("sent_does_not_replace_failed") is True and result.get("model_read_claimed") is False,
                result.get("inflight_error") is None and result.get("inflight_items") == [],
                result.get("revoked_items") == [] and result.get("revoked_snapshot_matches") is True and result.get("revoked_hides_old") is True,
                result.get("rotate_status") == 401 and result.get("rotate_version_delta") == 1 and result.get("rotate_text_absent") is True,
                result.get("disabled_rotate_enabled") is False and result.get("disabled_rotate_version") == result.get("disable_version") + 1,
                result.get("audit_status") == 503 and result.get("audit_code") == "AUDIT_UNAVAILABLE",
                result.get("audit_hides_text") is True and result.get("audit_not_stored") is True,
                result.get("credential_in_result") is False,
                result["http"].get("omitted_status") == 200 and result["http"].get("omitted_share") is False,
                result["http"].get("omitted_until") == UNTIL,
                result["http"].get("intent_share_provided") is False and result["http"].get("intent_until_provided") is False,
                result["http"].get("replay_status") == 200 and result["http"].get("replay_revision") == result["http"].get("first_revision"),
                result["http"].get("clear_status") == 409 and result["http"].get("provided_false_status") == 409,
                result["http"].get("client_status") == "pending" and result["http"].get("connected_label_in_agent") is False,
                result["http"].get("http_body_matches_snapshot") is True and result["http"].get("http_delivery") == "sent",
                result["http"].get("http_hit") is True and result["http"].get("list_has_response_body") is False,
                result["http"].get("detail_matches_snapshot") is True,
            ]
        )
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["pass"] = False
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if not result["pass"]:
        raise AssertionError(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    test_access_and_followup()
