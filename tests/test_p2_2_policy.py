"""P2.2: review inheritance, one visibility rule, and revocation before commit.

Access events stay P2.3. A real MCP client stays P2.4.
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
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "server"))

from zhiwo.adapters.kernel_client import connect
from zhiwo.api.app import DecisionBody
from zhiwo.api.errors import ApiError
from zhiwo.config import load_settings
from zhiwo.mcp_bridge import forward
from zhiwo.repositories.migrate import migrate
from zhiwo.services.agent_tools import explain_memory, get_context, propose_memory, search_memory
from zhiwo.services.agents import create_agent, rotate_credential, update_agent
from zhiwo.services.memories import get_memory
from zhiwo.services.publish import publish_memory
from zhiwo.services.review import decide_proposal

RESULT_PATH = REPO / "tests" / "results" / "p2_2_windows.json"
CACHE = REPO / "experiments" / "kernel_spike" / "runs" / "p0_3" / "fastembed-cache"
UNTIL = "2099-06-01T00:00:00+00:00"
CANDIDATE_UNTIL = "2001-01-01T00:00:00+00:00"
NEW_UNTIL = "2097-03-01T00:00:00+00:00"
CHANGED_UNTIL = "2098-01-01T00:00:00+00:00"
PRIVATE = "继承探针：蓝色笔记本只在周日使用。"
PRIVATE_NEXT = "继承探针：蓝色笔记本只在周日上午使用。"
PRIVATE_EDIT = "继承探针：编辑后仍只在周日使用。"
PRIVATE_EXPIRY = "继承探针：只改有效期的那一条。"
PRIVATE_EXPIRY_NEXT = "继承探针：有效期已单独修改。"
SHARED = "权限探针：橙色文件夹放在第二层。"
SHARED_UPDATE = "权限探针：橙色文件夹已经换了句子。"
OTHER = "权限探针：身份类别不该给这个连接。"
HIDDEN = "权限探针：这本私有手册不能被读取。"
OLD_BODY = "解释探针：旧版本正文不进入解释。"
NEW_BODY = "解释探针：正式正文不进入解释。"
FRAGMENT = "已审核片段。"
OLD_FRAGMENT = "旧片段不进入当前解释。"
SOURCE_TAIL = "整份来源后半句不外发。"
LONG = "长句探针：" + ("甲" * 2001)


def _dump(body: DecisionBody) -> dict:
    return body.model_dump(exclude_unset=True)


def _source(db_path, content: str, kind: str = "paste") -> str:
    source_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    now = "2026-09-25T00:00:00+00:00"
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            """
            INSERT INTO sources (id, kind, name, content, content_hash, imported_at)
            VALUES (?, ?, NULL, ?, ?, ?)
            """,
            (source_id, kind, content, digest, now),
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


def _proposal(db_path, content: str, *, source_id: str, payload_extra: dict | None = None) -> str:
    proposal_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    payload = {"content": content, "kind": "fact", "category": "preference", "scope": None}
    if payload_extra:
        payload.update(payload_extra)
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
                json.dumps({"text": content[:12]}, ensure_ascii=False),
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
            SELECT revision, share_enabled, valid_until, kernel_id
            FROM memory_refs
            WHERE memory_id = ? AND lifecycle = 'active'
            """,
            (memory_id,),
        ).fetchone()
    finally:
        connection.close()


def _count(db_path, table: str) -> int:
    connection = sqlite3.connect(db_path)
    try:
        return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        connection.close()


def _evidence(db_path, proposal_id: str) -> dict:
    connection = sqlite3.connect(db_path)
    try:
        raw = connection.execute("SELECT evidence_json FROM proposals WHERE id = ?", (proposal_id,)).fetchone()[0]
    finally:
        connection.close()
    return json.loads(raw)


def _grant(db_path, agent_id: str, tools: list[str], categories: list[str]) -> None:
    update_agent(
        db_path,
        agent_id,
        str(uuid.uuid4()),
        {"allowed_tools": tools, "allowed_categories": categories},
    )


def _call(func) -> tuple[dict | None, ApiError | None]:
    try:
        return func(), None
    except ApiError as exc:
        return None, exc


def test_policy_and_inheritance() -> None:
    if sys.platform != "win32" or platform.system() != "Windows" or os.environ.get("WSL_DISTRO_NAME"):
        raise AssertionError("P2.2 requires native Windows")
    if not CACHE.exists():
        raise AssertionError(f"local embedding cache is missing: {CACHE}")
    owner = "p22-owner-" + uuid.uuid4().hex
    secrets: list[str] = [owner]
    result: dict = {
        "task": "P2.2",
        "platform": platform.platform(),
        "python": platform.python_version(),
        "service": "in-process official functions",
        "access_log": "NOT_RUN",
        "real_client": "NOT_RUN",
        "stage_complete": False,
    }
    try:
        with tempfile.TemporaryDirectory(prefix="zhiwo-p22-", ignore_cleanup_errors=True) as raw:
            os.environ["ZHIWO_DATA_DIR"] = raw
            os.environ["ZHIWO_OWNER_CREDENTIAL"] = owner
            os.environ["ZHIWO_FASTEMBED_CACHE_DIR"] = str(CACHE)
            os.environ["ZHIWO_HOST"] = "127.0.0.1"
            os.environ["HF_HUB_OFFLINE"] = "1"
            os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
            os.environ.pop("ZHIWO_KERNEL_CONNECT_ONLY", None)
            os.environ.pop("ZHIWO_EXTRACTOR_API_KEY", None)
            settings = load_settings()
            migrate(settings.control_db)
            kernel = connect(settings.kernel_dir, cache_dir=settings.fastembed_cache, connect_only=False)
            db = settings.control_db
            source_id = _source(db, "合成来源只供审核引用。")

            def save(content: str, *, share: bool, category: str = "preference", until: str | None = None, target: str | None = None, revision: int | None = None, evidence: str | None = None, sources: list[str] | None = None) -> dict:
                payload = {
                    "content": content,
                    "kind": "fact",
                    "category": category,
                    "share_enabled": share,
                    "valid_until": until,
                    "source_refs": sources or [],
                }
                if evidence is not None:
                    payload["approved_evidence"] = evidence
                if target is not None:
                    payload["target_id"] = target
                    payload["base_revision"] = revision
                return publish_memory(db, kernel, str(uuid.uuid4()), payload)

            private = save(PRIVATE, share=False, until=UNTIL)
            omitted = DecisionBody(
                decision="update",
                content=PRIVATE_NEXT,
                target_id=private["memory_id"],
                base_revision=private["revision"],
            )
            omitted_body = _dump(omitted)
            proposal_id = _proposal(
                db,
                PRIVATE_NEXT,
                source_id=source_id,
                payload_extra={"share_enabled": True, "valid_until": CANDIDATE_UNTIL},
            )
            inherited = decide_proposal(db, kernel, proposal_id, str(uuid.uuid4()), omitted_body)
            inherited_row = _active(db, private["memory_id"])
            explicit_body = _dump(
                DecisionBody(
                    decision="update",
                    content=PRIVATE_NEXT + "明确打开。",
                    target_id=private["memory_id"],
                    base_revision=inherited["revision"],
                    share_enabled=True,
                    valid_until=None,
                )
            )
            explicit_proposal = _proposal(db, PRIVATE_NEXT + "明确打开。", source_id=source_id)
            explicit = decide_proposal(db, kernel, explicit_proposal, str(uuid.uuid4()), explicit_body)
            explicit_row = _active(db, private["memory_id"])

            expiry_memory = save(PRIVATE_EXPIRY, share=False, until=UNTIL)
            expiry_proposal = _proposal(db, PRIVATE_EXPIRY_NEXT, source_id=source_id, payload_extra={"share_enabled": True})
            expiry_body = _dump(
                DecisionBody(
                    decision="update",
                    content=PRIVATE_EXPIRY_NEXT,
                    target_id=expiry_memory["memory_id"],
                    base_revision=1,
                    valid_until=CHANGED_UNTIL,
                )
            )
            decide_proposal(db, kernel, expiry_proposal, str(uuid.uuid4()), expiry_body)
            expiry_row = _active(db, expiry_memory["memory_id"])

            edit_memory = save(PRIVATE, share=False, until=UNTIL)
            edit_proposal = _proposal(
                db,
                PRIVATE_EDIT,
                source_id=source_id,
                payload_extra={"share_enabled": True, "valid_until": CANDIDATE_UNTIL},
            )
            edit_body = _dump(
                DecisionBody(
                    decision="edit",
                    content=PRIVATE_EDIT,
                    target_id=edit_memory["memory_id"],
                    base_revision=1,
                )
            )
            decide_proposal(db, kernel, edit_proposal, str(uuid.uuid4()), edit_body)
            edit_row = _active(db, edit_memory["memory_id"])

            before_bad = _active(db, edit_memory["memory_id"])["revision"]
            bad_proposal = _proposal(db, PRIVATE_EDIT, source_id=source_id)
            bad_body = _dump(
                DecisionBody(
                    decision="update",
                    content=PRIVATE_EDIT,
                    target_id=edit_memory["memory_id"],
                    base_revision=before_bad,
                    share_enabled=None,
                )
            )
            _, bad_error = _call(lambda: decide_proposal(db, kernel, bad_proposal, str(uuid.uuid4()), bad_body))
            after_bad = _active(db, edit_memory["memory_id"])["revision"]

            added_proposal = _proposal(
                db,
                "新增探针：公开的绿色茶杯。",
                source_id=source_id,
                payload_extra={"valid_until": NEW_UNTIL},
            )
            added = decide_proposal(
                db,
                kernel,
                added_proposal,
                str(uuid.uuid4()),
                _dump(DecisionBody(decision="accept")),
            )
            added_row = _active(db, added["memory_id"])
            private_add_proposal = _proposal(db, "新增探针：这条由审核明确设为仅自己可见。", source_id=source_id)
            private_add = decide_proposal(
                db,
                kernel,
                private_add_proposal,
                str(uuid.uuid4()),
                _dump(DecisionBody(decision="accept", share_enabled=False)),
            )
            private_add_row = _active(db, private_add["memory_id"])

            result.update(
                {
                    "omitted_keys": sorted(omitted_body),
                    "inherited_share": bool(inherited_row["share_enabled"]),
                    "inherited_until": inherited_row["valid_until"],
                    "explicit_share": bool(explicit_row["share_enabled"]),
                    "explicit_until": explicit_row["valid_until"],
                    "expiry_only_share": bool(expiry_row["share_enabled"]),
                    "expiry_only_until": expiry_row["valid_until"],
                    "edit_share": bool(edit_row["share_enabled"]),
                    "edit_until": edit_row["valid_until"],
                    "null_share_status": None if bad_error is None else bad_error.status,
                    "null_share_revision_unchanged": before_bad == after_bad,
                    "new_share": bool(added_row["share_enabled"]),
                    "new_until": added_row["valid_until"],
                    "new_explicit_private": bool(private_add_row["share_enabled"]),
                }
            )

            reader = create_agent(db, str(uuid.uuid4()), "合成连接丙")
            secret = reader["credential"]
            secrets.append(secret)
            _grant(db, reader["id"], ["get_context", "search_memory", "propose_memory", "explain_memory"], ["preference"])
            empty_agent = create_agent(db, str(uuid.uuid4()), "合成连接丁")
            empty_secret = empty_agent["credential"]
            secrets.append(empty_secret)
            search_only = create_agent(db, str(uuid.uuid4()), "合成连接戊")
            search_secret = search_only["credential"]
            secrets.append(search_secret)
            _grant(db, search_only["id"], ["search_memory"], ["preference"])

            shared = save(SHARED, share=True, until=None)
            identity = save(OTHER, share=True, category="identity")
            hidden = save(HIDDEN, share=False, until=None)
            explain_source = _source(db, FRAGMENT + SOURCE_TAIL)
            old = save(OLD_BODY, share=True, evidence=json.dumps({"text": OLD_FRAGMENT}, ensure_ascii=False), sources=[explain_source])
            current = save(
                NEW_BODY,
                share=True,
                target=old["memory_id"],
                revision=1,
                evidence=json.dumps({"text": FRAGMENT}, ensure_ascii=False),
                sources=[explain_source],
            )
            visible_source = _source(db, "可见来源里的片段。其余句子不外发。")
            cited = save("权限探针：可以引用的共享记忆。", share=True, sources=[visible_source])
            private_source = _source(db, "私有来源全文不外发。短片段。")
            save(HIDDEN + "来源", share=False, sources=[private_source])

            calls = {"n": 0}

            def spy(_query, _top_k):
                calls["n"] += 1
                return [
                    {"id": hidden["kernel_id"], "content": HIDDEN},
                    {"id": identity["kernel_id"], "content": OTHER},
                    {"id": shared["kernel_id"], "content": SHARED},
                    {"id": "not-a-published-row", "content": "未发布行不该出现。"},
                ]

            searched = search_memory(db, kernel, secret, "橙色", recall=spy, limit=10)
            calls_after_search = calls["n"]
            context = get_context(db, kernel, secret, "橙色", max_items=5, recall=spy)
            before_denied = calls["n"]
            denied, denied_error = _call(lambda: get_context(db, kernel, search_secret, "橙色", recall=spy))
            calls_after_denied = calls["n"]
            _, empty_error = _call(lambda: search_memory(db, kernel, empty_secret, "橙色", recall=spy))
            long_calls = {"n": 0}

            def long_spy(_query, _top_k):
                long_calls["n"] += 1
                return [{"id": shared["kernel_id"], "content": LONG}, {"id": shared["kernel_id"], "content": SHARED}]

            limited = search_memory(db, kernel, secret, "橙色", recall=long_spy, limit=10)
            two = search_memory(
                db,
                kernel,
                secret,
                "橙色",
                limit=1,
                recall=lambda _query, _top_k: [
                    {"id": shared["kernel_id"], "content": SHARED},
                    {"id": cited["kernel_id"], "content": "权限探针：可以引用的共享记忆。"},
                ],
            )
            real = search_memory(db, kernel, secret, SHARED, limit=5)
            bridged = forward("search_memory", {"query": SHARED, "limit": 5}, db_path=db, kernel=kernel, credential=secret)
            _, unknown_tool = _call(
                lambda: forward(
                    "delete_memory",
                    {"query": SHARED},
                    db_path=db,
                    kernel=kernel,
                    credential=secret,
                )
            )
            _, extra_arg = _call(
                lambda: forward(
                    "search_memory",
                    {"query": SHARED, "include_all": True},
                    db_path=db,
                    kernel=kernel,
                    credential=secret,
                )
            )
            explained = explain_memory(db, kernel, secret, current["memory_id"])
            _, missing = _call(lambda: explain_memory(db, kernel, secret, str(uuid.uuid4())))
            _, hidden_explain = _call(lambda: explain_memory(db, kernel, secret, hidden["memory_id"]))
            refs_before = _count(db, "memory_refs")
            proposed = propose_memory(
                db,
                kernel,
                secret,
                str(uuid.uuid4()),
                {"type": "add", "content": "提案探针：希望记住绿色封面。", "kind": "fact", "category": "preference"},
                {"text": "希望记住绿色封面"},
            )
            refs_after = _count(db, "memory_refs")
            replay_key = str(uuid.uuid4())
            change = {
                "type": "update",
                "content": "提案探针：更新橙色文件夹的说法。",
                "kind": "fact",
                "category": "preference",
                "target_id": shared["memory_id"],
                "base_revision": shared["revision"],
            }
            evidence = {"text": "可见来源里的片段。", "source_ref": visible_source}
            updated = propose_memory(db, kernel, secret, replay_key, change, evidence)
            replayed = propose_memory(db, kernel, secret, replay_key, change, evidence)
            _, conflicted = _call(
                lambda: propose_memory(
                    db,
                    kernel,
                    secret,
                    replay_key,
                    {**change, "content": "另一句"},
                    evidence,
                )
            )
            _, private_target = _call(
                lambda: propose_memory(
                    db,
                    kernel,
                    secret,
                    str(uuid.uuid4()),
                    {
                        "type": "update",
                        "content": "不该针对私有记忆提案。",
                        "kind": "fact",
                        "category": "preference",
                        "target_id": hidden["memory_id"],
                        "base_revision": hidden["revision"],
                    },
                    {"text": "不该针对私有记忆"},
                )
            )
            _, absent_target = _call(
                lambda: propose_memory(
                    db,
                    kernel,
                    secret,
                    str(uuid.uuid4()),
                    {
                        "type": "update",
                        "content": "没有这条记忆。",
                        "kind": "fact",
                        "category": "preference",
                        "target_id": str(uuid.uuid4()),
                        "base_revision": 1,
                    },
                    {"text": "没有这条记忆"},
                )
            )
            unverified = propose_memory(
                db,
                kernel,
                secret,
                str(uuid.uuid4()),
                {"type": "add", "content": "提案探针：未核实证据。", "kind": "fact", "category": "preference"},
                {"text": "短片段。", "source_ref": private_source},
            )
            matched_record = _evidence(db, updated["proposal_id"])
            unverified_record = _evidence(db, unverified["proposal_id"])
            claim_connection = sqlite3.connect(db)
            try:
                claim_content = claim_connection.execute(
                    """
                    SELECT sources.content FROM sources
                    JOIN proposals ON proposals.source_id = sources.id
                    WHERE proposals.id = ?
                    """,
                    (unverified["proposal_id"],),
                ).fetchone()[0]
            finally:
                claim_connection.close()

            def revoke(secret_value: str, kernel_id: str, content: str, mutate) -> tuple[dict | None, ApiError | None]:
                return _call(
                    lambda: search_memory(
                        db,
                        kernel,
                        secret_value,
                        content,
                        recall=lambda _query, _top_k: [{"id": kernel_id, "content": content}],
                        before_commit=mutate,
                    )
                )

            unshare_target = save("权限探针：即将停止共享。", share=True)
            unshared, unshare_error = revoke(
                secret,
                unshare_target["kernel_id"],
                "权限探针：即将停止共享。",
                lambda: save(
                    "权限探针：即将停止共享。",
                    share=False,
                    target=unshare_target["memory_id"],
                    revision=1,
                ),
            )
            replace_target = save(SHARED, share=True)
            replaced, replace_error = revoke(
                secret,
                replace_target["kernel_id"],
                SHARED,
                lambda: save(
                    SHARED_UPDATE,
                    share=True,
                    target=replace_target["memory_id"],
                    revision=1,
                ),
            )
            disable_agent = create_agent(db, str(uuid.uuid4()), "合成连接己")
            disable_secret = disable_agent["credential"]
            secrets.append(disable_secret)
            _grant(db, disable_agent["id"], ["search_memory"], ["preference"])
            _, disabled_error = revoke(
                disable_secret,
                shared["kernel_id"],
                SHARED,
                lambda: update_agent(db, disable_agent["id"], str(uuid.uuid4()), {"enabled": False}),
            )
            rotate_agent = create_agent(db, str(uuid.uuid4()), "合成连接庚")
            rotate_secret = rotate_agent["credential"]
            secrets.append(rotate_secret)
            _grant(db, rotate_agent["id"], ["search_memory"], ["preference"])
            version_before = update_agent(db, rotate_agent["id"], str(uuid.uuid4()), {"name": "合成连接庚不变"})["policy_version"]
            _, rotated_error = revoke(
                rotate_secret,
                shared["kernel_id"],
                SHARED,
                lambda: rotate_credential(db, rotate_agent["id"], str(uuid.uuid4())),
            )
            version_after = update_agent(db, rotate_agent["id"], str(uuid.uuid4()), {"name": "合成连接庚仍不变"})["policy_version"]
            stopped = create_agent(db, str(uuid.uuid4()), "合成连接辛")
            stopped_secret = stopped["credential"]
            secrets.append(stopped_secret)
            _grant(db, stopped["id"], ["search_memory"], ["preference"])
            update_agent(db, stopped["id"], str(uuid.uuid4()), {"enabled": False})
            stopped_version = rotate_credential(db, stopped["id"], str(uuid.uuid4()))
            secrets.append(stopped_version["credential"])

            serialized = {
                "searched": searched,
                "context": context,
                "limited": limited,
                "two": two,
                "real": real,
                "bridged": bridged,
                "proposed": proposed,
                "unshared": unshared,
                "replaced": replaced,
            }
            blob = json.dumps(serialized, ensure_ascii=False)
            explain_blob = json.dumps(explained, ensure_ascii=False)
            result.update(
                {
                    "same_filter_count": len(searched["items"]) == len(context["items"]) == 1,
                    "returned_shared": searched["items"][0]["content"] == SHARED if searched["items"] else False,
                    "context_shared": context["items"][0]["content"] == SHARED if context["items"] else False,
                    "hidden_absent": HIDDEN not in blob and OTHER not in blob and "未发布行不该出现。" not in blob,
                    "recall_once_for_filtered_window": calls_after_search == 1,
                    "denied_tool_status": None if denied_error is None else denied_error.status,
                    "denied_did_not_recall": calls_after_denied == before_denied,
                    "denied_body": None if denied is None else denied,
                    "empty_grant_status": None if empty_error is None else empty_error.status,
                    "long_not_sliced": LONG not in blob and all(len(item["content"]) <= 2000 for item in limited["items"]),
                    "long_truncated": limited["truncated"] is True,
                    "long_recall_once": long_calls["n"] == 1,
                    "limit_one_truncated": two["truncated"] is True and len(two["items"]) == 1,
                    "real_search_hit": any(item["content"] == SHARED for item in real["items"]),
                    "bridge_hit": any(item["content"] == SHARED for item in bridged["items"]),
                    "unknown_tool_status": None if unknown_tool is None else unknown_tool.status,
                    "extra_argument_status": None if extra_arg is None else extra_arg.status,
                    "explain_evidence": explained["result"]["evidence"],
                    "explain_source_kind": explained["result"]["source_kind"],
                    "explain_keys": sorted(explained["result"]),
                    "explain_hides_source_and_history": SOURCE_TAIL not in explain_blob
                    and OLD_BODY not in explain_blob
                    and NEW_BODY not in explain_blob
                    and OLD_FRAGMENT not in explain_blob,
                    "hidden_matches_missing": hidden_explain is not None
                    and missing is not None
                    and hidden_explain.status == missing.status
                    and hidden_explain.message == missing.message,
                    "propose_status": proposed["status"],
                    "propose_did_not_publish": refs_before == refs_after,
                    "propose_replay": replayed["proposal_id"] == updated["proposal_id"],
                    "propose_conflict": None if conflicted is None else conflicted.status,
                    "private_target_status": None if private_target is None else private_target.status,
                    "private_target_message": None if private_target is None else private_target.message,
                    "missing_target_message": None if absent_target is None else absent_target.message,
                    "matched_verification": matched_record.get("verification"),
                    "unverified_note": unverified_record.get("note"),
                    "claim_is_fragment_only": claim_content == "短片段。",
                    "unshare_error": None if unshare_error is None else unshare_error.status,
                    "unshare_items": [] if unshared is None else [item["content"] for item in unshared["items"]],
                    "replace_error": None if replace_error is None else replace_error.status,
                    "replace_items": [] if replaced is None else [item["content"] for item in replaced["items"]],
                    "disable_status": None if disabled_error is None else disabled_error.status,
                    "disable_message": None if disabled_error is None else disabled_error.message,
                    "rotate_status": None if rotated_error is None else rotated_error.status,
                    "rotate_message": None if rotated_error is None else rotated_error.message,
                    "rotate_version_before": version_before,
                    "rotate_version_after": version_after,
                    "rotate_keeps_disabled": stopped_version["enabled"] is False,
                    "rotate_disabled_version": stopped_version["policy_version"],
                }
            )
            view = get_memory(db, kernel, private["memory_id"])
            result["owner_still_reads_private"] = view["content"] == PRIVATE_NEXT + "明确打开。" and view["share_enabled"] is True
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    text = json.dumps(result, ensure_ascii=False)
    for secret in secrets:
        text = text.replace(secret, "[redacted]")
    result = json.loads(text)
    result["credential_in_result"] = any(secret in text for secret in secrets)
    checks = [
        result.get("omitted_keys") == ["base_revision", "content", "decision", "target_id"],
        result.get("inherited_share") is False and result.get("inherited_until") == UNTIL,
        result.get("explicit_share") is True and result.get("explicit_until") is None,
        result.get("expiry_only_share") is False and result.get("expiry_only_until") == CHANGED_UNTIL,
        result.get("edit_share") is False and result.get("edit_until") == UNTIL,
        result.get("null_share_status") == 400 and result.get("null_share_revision_unchanged") is True,
        result.get("new_share") is True and result.get("new_until") == NEW_UNTIL,
        result.get("new_explicit_private") is False,
        result.get("same_filter_count") is True,
        result.get("returned_shared") is True and result.get("context_shared") is True,
        result.get("hidden_absent") is True,
        result.get("recall_once_for_filtered_window") is True,
        result.get("denied_tool_status") == 403 and result.get("denied_did_not_recall") is True and result.get("denied_body") is None,
        result.get("empty_grant_status") == 403,
        result.get("long_not_sliced") is True and result.get("long_truncated") is True and result.get("long_recall_once") is True,
        result.get("limit_one_truncated") is True,
        result.get("real_search_hit") is True and result.get("bridge_hit") is True,
        result.get("unknown_tool_status") == 400 and result.get("extra_argument_status") == 400,
        result.get("explain_evidence") == FRAGMENT and result.get("explain_source_kind") == "paste",
        result.get("explain_keys") == ["confirmed_at", "evidence", "id", "revision", "source_kind"],
        result.get("explain_hides_source_and_history") is True,
        result.get("hidden_matches_missing") is True,
        result.get("propose_status") == "pending" and result.get("propose_did_not_publish") is True,
        result.get("propose_replay") is True and result.get("propose_conflict") == 409,
        result.get("private_target_status") == 404,
        result.get("private_target_message") == result.get("missing_target_message") == "memory not found",
        result.get("matched_verification") == "matched",
        result.get("unverified_note") == "Agent 提供，未核实",
        result.get("claim_is_fragment_only") is True,
        result.get("unshare_error") is None and result.get("unshare_items") == [],
        result.get("replace_error") is None and SHARED not in result.get("replace_items", []) and SHARED_UPDATE not in result.get("replace_items", []),
        result.get("disable_status") == 403 and SHARED not in (result.get("disable_message") or ""),
        result.get("rotate_status") == 401 and SHARED not in (result.get("rotate_message") or ""),
        result.get("rotate_version_after") == (result.get("rotate_version_before") or 0) + 1,
        result.get("rotate_keeps_disabled") is True and result.get("rotate_disabled_version", 0) >= 2,
        result.get("owner_still_reads_private") is True,
        result.get("credential_in_result") is False,
        "error" not in result,
    ]
    result["pass"] = all(checks)
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if not result["pass"]:
        raise AssertionError(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    test_policy_and_inheritance()
