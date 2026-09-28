"""Review-edge cases from the 2026-09-27 audit plan.

Runs in a temporary library. It does not read or write the owner's real data.
"""

from __future__ import annotations

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
from zhiwo.api.errors import ApiError
from zhiwo.config import load_settings
from zhiwo.repositories.migrate import migrate
from zhiwo.services.agent_tools import explain_memory, get_context, propose_memory, search_memory
from zhiwo.services.agents import create_agent, update_agent
from zhiwo.services.deletion import delete_memory
from zhiwo.services.memories import get_memory
from zhiwo.services.publish import publish_memory
from zhiwo.services.review import decide_proposal

RESULT_PATH = REPO / "tests" / "results" / "review_edges.json"
CACHE = REPO / "experiments" / "kernel_spike" / "runs" / "p0_3" / "fastembed-cache"
LIME = "测试标记青柠回声：回答前先用一句话复述我的要求。"
CATEGORIES = ["identity", "goal", "preference", "project", "event", "other"]
TOOLS = ["get_context", "search_memory", "propose_memory", "explain_memory"]


def test_review_edges() -> None:
    if sys.platform != "win32" or platform.system() != "Windows" or os.environ.get("WSL_DISTRO_NAME"):
        raise AssertionError("review edge cases require native Windows")
    if not CACHE.exists():
        raise AssertionError(f"local embedding cache is missing: {CACHE}")
    owner = "review-edges-" + uuid.uuid4().hex
    result: dict = {
        "task": "review-edges",
        "platform": platform.platform(),
        "python": platform.python_version(),
        "library": "temporary",
        "real_user_library": False,
        "cases": [],
    }
    failures: list[str] = []
    try:
        with tempfile.TemporaryDirectory(prefix="zhiwo-review-edges-", ignore_cleanup_errors=True) as raw:
            os.environ["ZHIWO_DATA_DIR"] = raw
            os.environ["ZHIWO_OWNER_CREDENTIAL"] = owner
            os.environ["ZHIWO_FASTEMBED_CACHE_DIR"] = str(CACHE)
            os.environ["ZHIWO_HOST"] = "127.0.0.1"
            os.environ["HF_HUB_OFFLINE"] = "1"
            os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
            os.environ.pop("ZHIWO_KERNEL_CONNECT_ONLY", None)
            os.environ.pop("ZHIWO_EXTRACTOR_API_KEY", None)
            settings = load_settings()
            result["schema_version"] = migrate(settings.control_db)
            kernel = connect(settings.kernel_dir, cache_dir=settings.fastembed_cache, connect_only=False)
            db = settings.control_db
            agent = create_agent(db, str(uuid.uuid4()), "审核边界连接")
            update_agent(db, agent["id"], str(uuid.uuid4()), {"allowed_tools": TOOLS, "allowed_categories": CATEGORIES})
            credential = agent["credential"]

            def note(case: str, name: str, expected: str, actual: str, verdict: str, **ids) -> None:
                result["cases"].append(
                    {
                        "id": case,
                        "name": name,
                        "expected": expected,
                        "actual": actual,
                        "verdict": verdict,
                        **ids,
                    }
                )

            def fail(message: str) -> None:
                failures.append(message)

            def save(content: str, *, category: str = "preference", share: bool = True) -> dict:
                return publish_memory(
                    db,
                    kernel,
                    str(uuid.uuid4()),
                    {
                        "content": content,
                        "kind": "fact",
                        "category": category,
                        "share_enabled": share,
                        "source_refs": [],
                    },
                )

            def propose(change: dict, evidence: str, request_id: str | None = None):
                key = request_id or str(uuid.uuid4())
                try:
                    body = propose_memory(db, kernel, credential, key, change, {"text": evidence})
                    return key, body, None
                except ApiError as exc:
                    return key, None, exc

            def decide(proposal_id: str, body: dict):
                try:
                    return decide_proposal(db, kernel, proposal_id, str(uuid.uuid4()), body), None
                except ApiError as exc:
                    return None, exc

            def memory(memory_id: str) -> dict | None:
                try:
                    return get_memory(db, kernel, memory_id)
                except ApiError:
                    return None

            def proposal(proposal_id: str):
                connection = sqlite3.connect(db)
                connection.row_factory = sqlite3.Row
                try:
                    return connection.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
                finally:
                    connection.close()

            def reader():
                # A publish closes Mnemosyne's thread-local connection, so recall
                # needs a handle opened after the last write.
                return connect(settings.kernel_dir, cache_dir=settings.fastembed_cache, connect_only=False)

            def hits(query: str) -> list[str]:
                found = search_memory(db, reader(), credential, query, limit=10)
                return [item.get("content") or "" for item in found.get("items") or []]

            def context(task: str) -> list[str]:
                found = get_context(db, reader(), credential, task, max_items=5)
                return [item.get("content") or "" for item in found.get("items") or []]

            review_source = (REPO / "apps" / "web" / "src" / "Review.tsx").read_text(encoding="utf-8")
            ui_closed = (
                "按当前版本更新" not in review_source
                and "adoptCurrent" not in review_source
                and "这条建议不能再改它" in review_source
                and "proposal.base_revision" in review_source
            )

            origin = save("[T01] 原句：周末骑行四十公里。")
            key_a, first, first_error = propose(
                _update("[T01-a] 改成清晨骑行。", origin),
                "A批 T01 2026-09-27",
            )
            key_b, second, second_error = propose(
                _update("[T01-b] 改成夜晚骑行。", origin),
                "A批 T01 2026-09-27",
            )
            both_pending = (
                first_error is None
                and second_error is None
                and first["status"] == "pending"
                and second["status"] == "pending"
                and first["proposal_id"] != second["proposal_id"]
            )
            approved, approve_error = decide(
                first["proposal_id"],
                {"decision": "update", "target_id": origin["memory_id"], "base_revision": 1, "content": "[T01-a] 改成清晨骑行。"},
            )
            stale, stale_error = decide(
                second["proposal_id"],
                {"decision": "update", "target_id": origin["memory_id"], "base_revision": 1, "content": "[T01-b] 改成夜晚骑行。"},
            )
            after_stale = memory(origin["memory_id"])
            stale_row = proposal(second["proposal_id"])
            kept, keep_error = decide(
                second["proposal_id"],
                {"decision": "keep_both", "scope": "夜晚骑行", "content": "[T01-b] 改成夜晚骑行。"},
            )
            after_keep = memory(origin["memory_id"])
            kept_memory = None if keep_error or not kept else memory(kept["memory_id"])
            t01_safe = (
                approve_error is None
                and approved["revision"] == 2
                and stale is None
                and stale_error is not None
                and stale_error.code == "CONFLICT"
                and after_stale["content"] == "[T01-a] 改成清晨骑行。"
                and after_stale["revision"] == 2
                and stale_row["status"] == "pending"
                and keep_error is None
                and after_keep["content"] == "[T01-a] 改成清晨骑行。"
                and after_keep["revision"] == 2
                and kept_memory is not None
                and kept_memory["content"] == "[T01-b] 改成夜晚骑行。"
                and kept_memory["id"] != origin["memory_id"]
            )
            if not t01_safe:
                fail("T01 stale update changed the current memory or keep-both overwrote it")
            note(
                "T01",
                "同目标同批双改",
                "两条都可先进入待确认；用过期版本批准时不得覆盖；另存不影响原记忆",
                f"双 pending={both_pending}；先通过后版本 {None if not approved else approved.get('revision')}；过期批准={_err(stale_error)}；当前仍是清晨且版本 2={after_stale['content'] if after_stale else None}/{None if not after_stale else after_stale['revision']}；另存后原句未变={after_keep['content'] if after_keep else None}；界面不再提供按当前版本覆盖={ui_closed}",
                "通过" if t01_safe and ui_closed and both_pending else "缺陷",
                proposal_id=f"{first['proposal_id']}|{second['proposal_id']}",
                request_id=f"{key_a}|{key_b}",
                target_id=origin["memory_id"],
            )

            owned = save("[T01] 用户先改过的原句。")
            _, owner_proposal, owner_error = propose(
                _update("[T01] 提案想改成另一句。", owned),
                "A批 T01 用户先改 2026-09-27",
            )
            owner_saved = save_update(db, kernel, owned, "[T01] 用户自己已保存的新句。")
            _, owner_conflict = decide(
                owner_proposal["proposal_id"],
                {
                    "decision": "update",
                    "target_id": owned["memory_id"],
                    "base_revision": 1,
                    "content": "[T01] 提案想改成另一句。",
                },
            )
            owner_now = memory(owned["memory_id"])
            owner_safe = (
                owner_error is None
                and owner_saved["revision"] == 2
                and owner_conflict is not None
                and owner_conflict.code == "CONFLICT"
                and owner_now["content"] == "[T01] 用户自己已保存的新句。"
                and owner_now["revision"] == 2
            )
            if not owner_safe:
                fail("owner edit was overwritten by a stale proposal")
            note(
                "T01-owner",
                "用户先改正式记忆，旧提案再批准",
                "正式记忆保持用户刚保存的句子",
                f"用户版本={owner_saved.get('revision')}；旧提案={_err(owner_conflict)}；当前={owner_now['content'] if owner_now else None}",
                "通过" if owner_safe else "缺陷 S0",
                proposal_id=owner_proposal["proposal_id"],
                target_id=owned["memory_id"],
            )

            ladder = save("[T02] 阶梯起点。")
            _, v2, v2_error = propose(_update("[T02] 第二版。", ladder, revision=1), "A批 T02 2026-09-27")
            v2_done, v2_decide = decide(v2["proposal_id"], _update_body(ladder["memory_id"], 1, "[T02] 第二版。"))
            _, v3, v3_error = propose(_update("[T02] 第三版。", ladder, revision=2), "A批 T02 2026-09-27")
            v3_done, v3_decide = decide(v3["proposal_id"], _update_body(ladder["memory_id"], 2, "[T02] 第三版。"))
            _, v4, v4_error = propose(_update("[T02] 过期攻击。", ladder, revision=2), "A批 T02 2026-09-27")
            ladder_now = memory(ladder["memory_id"])
            t02_safe = (
                v2_error is None
                and v3_error is None
                and v2_decide is None
                and v3_decide is None
                and v2_done["revision"] == 2
                and v3_done["revision"] == 3
                and v4 is None
                and v4_error is not None
                and v4_error.code == "CONFLICT"
                and ladder_now["content"] == "[T02] 第三版。"
                and ladder_now["revision"] == 3
            )
            if not t02_safe:
                fail("T02 stale revision was accepted")
            note(
                "T02",
                "连续修改阶梯",
                "前两步通过并递增版本；过期版本在提交时被拒绝，正式记忆保持第三版",
                f"v2={None if not v2_done else v2_done.get('revision')}；v3={None if not v3_done else v3_done.get('revision')}；过期提交={_err(v4_error)}；当前={ladder_now['content'] if ladder_now else None}",
                "通过" if t02_safe else "缺陷 S0",
                proposal_id=f"{v2['proposal_id']}|{v3['proposal_id']}",
                target_id=ladder["memory_id"],
            )

            crossed = save("[T03] 原来是偏好事实。")
            _, bad_kind, bad_kind_error = propose(
                {
                    "type": "update",
                    "content": "[T03] 想改类别。",
                    "kind": "skill",
                    "category": "goal",
                    "target_id": crossed["memory_id"],
                    "base_revision": 1,
                },
                "A批 T03 2026-09-27",
            )
            _, moved, moved_error = propose(
                {
                    "type": "update",
                    "content": "[T03] 类别改成目标。",
                    "kind": "fact",
                    "category": "goal",
                    "target_id": crossed["memory_id"],
                    "base_revision": 1,
                },
                "A批 T03 2026-09-27",
            )
            moved_done, moved_decide = decide(
                moved["proposal_id"],
                _update_body(crossed["memory_id"], 1, "[T03] 类别改成目标。"),
            )
            moved_now = memory(crossed["memory_id"])
            t03_clear = bad_kind is None and bad_kind_error is not None and bad_kind_error.code == "VALIDATION_ERROR"
            t03_category = (
                moved_error is None
                and moved_decide is None
                and moved_now["category"] == "goal"
                and moved_now["kind"] == "fact"
                and moved_now["content"] == "[T03] 类别改成目标。"
            )
            note(
                "T03",
                "跨字段修改",
                "非法 kind 拒绝；合法 category 变更在批准后生效",
                f"非法 kind={_err(bad_kind_error)}；批准后 category={moved_now['category'] if moved_now else None} kind={moved_now['kind'] if moved_now else None} revision={None if not moved_done else moved_done.get('revision')}",
                "通过" if t03_clear and t03_category else "缺陷 S2",
                proposal_id=None if moved_error else moved["proposal_id"],
                target_id=crossed["memory_id"],
            )

            same = save("[T04] 逐字相同的原句。")
            _, noop, noop_error = propose(_update("[T04] 逐字相同的原句。", same), "A批 T04 2026-09-27")
            noop_done, noop_decide = decide(noop["proposal_id"], _update_body(same["memory_id"], 1, "[T04] 逐字相同的原句。"))
            same_now = memory(same["memory_id"])
            note(
                "T04",
                "空修改",
                "方案希望无变化不产生新版本；产品未要求拒绝",
                f"提案={None if noop_error else noop['status']}；批准版本={None if not noop_done else noop_done.get('revision')}；正文未变={same_now['content'] == '[T04] 逐字相同的原句。' if same_now else False}",
                "记录 S3：相同正文仍新增一个版本" if noop_decide is None and same_now and same_now["revision"] == 2 else "通过",
                proposal_id=None if noop_error else noop["proposal_id"],
                target_id=same["memory_id"],
            )

            rolled = save("[T05] 最初的句子。")
            changed = save_update(db, kernel, rolled, "[T05] 中间改过的句子。")
            _, back, back_error = propose(_update("[T05] 最初的句子。", rolled, revision=changed["revision"]), "A批 T05 2026-09-27")
            back_done, back_decide = decide(
                back["proposal_id"],
                _update_body(rolled["memory_id"], changed["revision"], "[T05] 最初的句子。"),
            )
            rolled_now = memory(rolled["memory_id"])
            t05_ok = back_error is None and back_decide is None and rolled_now["content"] == "[T05] 最初的句子。" and rolled_now["revision"] == 3
            note(
                "T05",
                "改回旧版",
                "用当前版本号可以把正文改回旧句，并产生新版本",
                f"中间版本={changed.get('revision')}；改回版本={None if not back_done else back_done.get('revision')}；正文={rolled_now['content'] if rolled_now else None}",
                "通过" if t05_ok else "缺陷",
                proposal_id=None if back_error else back["proposal_id"],
                target_id=rolled["memory_id"],
            )

            replay_key = str(uuid.uuid4())
            replay_change = {"type": "add", "content": "[T06] 同一请求。", "kind": "fact", "category": "preference"}
            _, replay_first, replay_first_error = propose(replay_change, "B批 T06 2026-09-27", replay_key)
            _, replay_second, replay_second_error = propose(replay_change, "B批 T06 2026-09-27", replay_key)
            _, replay_other, replay_other_error = propose(
                {"type": "add", "content": "[T06] 同一请求但另一句。", "kind": "fact", "category": "preference"},
                "B批 T06 2026-09-27",
                replay_key,
            )
            t06_ok = (
                replay_first_error is None
                and replay_second_error is None
                and replay_first["proposal_id"] == replay_second["proposal_id"]
                and replay_other is None
                and replay_other_error is not None
                and replay_other_error.code == "CONFLICT"
            )
            if not t06_ok:
                fail("T06 replay created a second proposal or accepted a different payload")
            note(
                "T06",
                "request_id 重放",
                "同载荷返回原提案；不同载荷冲突",
                f"两次 id 相同={replay_first['proposal_id'] == replay_second['proposal_id']}；不同载荷={_err(replay_other_error)}",
                "通过" if t06_ok else "缺陷 S0",
                proposal_id=replay_first["proposal_id"],
                request_id=replay_key,
            )

            clone = "[T07] 唯一文本A"
            _, clone_a, clone_a_error = propose(
                {"type": "add", "content": clone, "kind": "fact", "category": "preference"},
                "B批 T07 2026-09-27",
            )
            _, clone_b, clone_b_error = propose(
                {"type": "add", "content": clone, "kind": "fact", "category": "preference"},
                "B批 T07 2026-09-27",
            )
            note(
                "T07",
                "内容克隆",
                "方案希望审核侧提示重复；服务允许两条待确认",
                f"两条不同提案={clone_a_error is None and clone_b_error is None and clone_a['proposal_id'] != clone_b['proposal_id']}；服务不查重。待确认的相似判断只对比已加载的正式记忆，不对比其他待确认",
                "记录 S2：两条相同新增都会作为普通新增",
                proposal_id=f"{clone_a['proposal_id']}|{clone_b['proposal_id']}",
            )

            lime = save(LIME)
            _, exact, exact_error = propose(
                {"type": "add", "content": LIME, "kind": "fact", "category": "preference"},
                "B批 T08 2026-09-27",
            )
            prefixed = "[T08] " + LIME
            _, prefixed_proposal, prefixed_error = propose(
                {"type": "add", "content": prefixed, "kind": "fact", "category": "preference"},
                "B批 T08 2026-09-27",
            )
            note(
                "T08",
                "与正式库撞车",
                "审核侧对逐字相同的新增给出相似提示",
                f"逐字提案={None if exact_error else exact['status']}；界面相似分={_overlap(LIME, LIME)}；带编号变体相似分={_overlap(prefixed, LIME)}。服务不拦截。正式记忆 id={lime['memory_id']}",
                "记录：服务不拦截；逐字相同会进入待确认的相似分支，不能直接改原来那一条",
                proposal_id=f"{exact['proposal_id']}|{prefixed_proposal['proposal_id']}",
                target_id=lime["memory_id"],
            )

            traditional = "測試標記青檸回聲：回答前先用一句話複述我的要求。"
            halfwidth = "测试标记青柠回声:回答前先用一句话复述我的要求."
            paraphrase = "[T09] 回答之前先把要求复述一遍。"
            variants = []
            for label, text in (("繁体", traditional), ("半角标点", halfwidth), ("同义改写", paraphrase)):
                _, made, made_error = propose(
                    {"type": "add", "content": text, "kind": "fact", "category": "preference"},
                    f"B批 T09 {label} 2026-09-27",
                )
                variants.append(f"{label}={'拒绝 ' + _err(made_error) if made_error else made['status']} 相似分={_overlap(text, LIME)}")
            note(
                "T09",
                "繁简与标点变体",
                "记录审核侧能识别到哪一种",
                "；".join(variants) + "。相似阈值是 0.62，只在待确认页面对比已加载正式记忆",
                "能力基线",
            )

            long_text = "[T10] " + ("测" * 3000)
            _, long_proposal, long_error = propose(
                {"type": "add", "content": long_text, "kind": "fact", "category": "preference"},
                "C批 T10 2026-09-27",
            )
            note(
                "T10",
                "超长文本",
                "拒绝并说明长度上限",
                f"提案={long_proposal}；{_err(long_error)}",
                "通过" if long_proposal is None and long_error is not None and "2000" in long_error.message else "缺陷 S2",
            )

            special = "[T11] 引号\"反斜杠\\换行\n{{占位符}}<script>alert(1)</script>\t制表😀"
            _, special_proposal, special_error = propose(
                {"type": "add", "content": special, "kind": "fact", "category": "preference"},
                "C批 T11 2026-09-27",
            )
            special_done, special_decide = (None, special_error)
            if special_error is None:
                special_done, special_decide = decide(special_proposal["proposal_id"], {"decision": "accept"})
            special_now = None if special_decide or not special_done else memory(special_done["memory_id"])
            special_hits = [] if special_now is None else hits("[T11]")
            t11_ok = special_now is not None and special_now["content"] == special
            if not t11_ok:
                fail("T11 stored content differs from the submitted text")
            note(
                "T11",
                "特殊字符",
                "批准后正文与提交逐字一致",
                f"入库一致={t11_ok}；搜索命中={[item == special for item in special_hits]}",
                "通过" if t11_ok else "缺陷 S0",
                proposal_id=None if special_error else special_proposal["proposal_id"],
                target_id=None if not special_done else special_done.get("memory_id"),
            )

            short_results = []
            for label, text in (("短句", "好。"), ("仅编号", "[T12]"), ("空格", " ")):
                _, made, made_error = propose(
                    {"type": "add", "content": text, "kind": "fact", "category": "preference"},
                    f"C批 T12 {label} 2026-09-27",
                )
                short_results.append(f"{label}={made['status'] if made_error is None else _err(made_error)}")
            note(
                "T12",
                "极短内容",
                "空内容拒绝；非空短句按 1 到 2000 字接受",
                "；".join(short_results),
                "通过" if "空格=400 VALIDATION_ERROR" in "；".join(short_results) and "短句=pending" in "；".join(short_results) else "缺陷 S2",
            )

            _, unrelated, unrelated_error = propose(
                {"type": "add", "content": "[T13] 重要：公司服务器机房在 B2 层", "kind": "fact", "category": "preference"},
                "今天天气晴朗，适合出门",
            )
            unrelated_row = None if unrelated_error else proposal(unrelated["proposal_id"])
            evidence = {} if unrelated_row is None else json.loads(unrelated_row["evidence_json"])
            note(
                "T13",
                "伪证据",
                "记录证据是否只做形式校验",
                f"提案={None if unrelated_error else unrelated['status']}；verification={evidence.get('verification')}；note={evidence.get('note')}。待确认只在证据文本为空时提示缺少依据，不比较证据和正文是否相关",
                "记录 S3：无关证据可提交，页面不标红",
                proposal_id=None if unrelated_error else unrelated["proposal_id"],
            )

            fresh = save("[T14] 已存在、可被修改的句子。")
            _, pending_add, pending_add_error = propose(
                {"type": "add", "content": "[T14] 还没批准的新增。", "kind": "fact", "category": "goal"},
                "D批 T14 2026-09-27",
            )
            _, aimed, aimed_error = propose(
                _update("[T14] 想改那条还没批准的新增。", {"memory_id": pending_add["proposal_id"], "revision": 1}),
                "D批 T14 2026-09-27",
            )
            _, real_update, real_update_error = propose(
                _update("[T14] 修改已存在的句子。", fresh),
                "D批 T14 2026-09-27",
            )
            real_done, real_decide = decide(
                real_update["proposal_id"],
                _update_body(fresh["memory_id"], 1, "[T14] 修改已存在的句子。"),
            )
            fresh_now = memory(fresh["memory_id"])
            add_row = proposal(pending_add["proposal_id"])
            t14_ok = (
                pending_add_error is None
                and aimed is None
                and aimed_error is not None
                and aimed_error.code == "NOT_FOUND"
                and real_decide is None
                and fresh_now["content"] == "[T14] 修改已存在的句子。"
                and add_row["status"] == "pending"
                and memory(pending_add["proposal_id"]) is None
            )
            if not t14_ok:
                fail("T14 created an orphan or lost the pending add")
            note(
                "T14",
                "先审修改、新增仍待确认",
                "不能把未批准提案当修改目标；先批准另一条修改不产生孤儿",
                f"指向提案={_err(aimed_error)}；已存在记忆改为={fresh_now['content'] if fresh_now else None} 版本={fresh_now['revision'] if fresh_now else None}；新增仍={add_row['status']}",
                "通过" if t14_ok else "缺陷 S0",
                proposal_id=f"{pending_add['proposal_id']}|{real_update['proposal_id']}",
                target_id=fresh["memory_id"],
            )

            _, rejected, rejected_error = propose(
                {"type": "add", "content": "[T15] 准备被拒绝。", "kind": "fact", "category": "preference"},
                "D批 T15 2026-09-27",
            )
            reject_done, reject_error = decide(rejected["proposal_id"], {"decision": "reject"})
            _, rejected_update, rejected_update_error = propose(
                _update("[T15] 拒绝后再改。", {"memory_id": rejected["proposal_id"], "revision": 1}),
                "D批 T15 2026-09-27",
            )
            _, rejected_explain = _call(lambda: explain_memory(db, kernel, credential, rejected["proposal_id"]))
            rejected_row = proposal(rejected["proposal_id"])
            t15_ok = (
                rejected_error is None
                and reject_error is None
                and reject_done["status"] == "rejected"
                and rejected_row["status"] == "rejected"
                and rejected_update is None
                and rejected_update_error is not None
                and rejected_update_error.code == "NOT_FOUND"
                and memory(rejected["proposal_id"]) is None
            )
            if not t15_ok:
                fail("T15 wrote a rejected proposal into memory")
            note(
                "T15",
                "拒绝后再改",
                "拒绝后不能写入；提示不需要区分已拒绝和不存在",
                f"拒绝={reject_done}；再改={_err(rejected_update_error)}；解释={_err(rejected_explain)}",
                "通过；提示是 memory not found，不写已被拒绝",
                proposal_id=rejected["proposal_id"],
            )

            hidden = save("[T16] 仅自己可见的句子。", share=False)
            _, pending_explain = _call(lambda: explain_memory(db, kernel, credential, pending_add["proposal_id"]))
            _, hidden_explain = _call(lambda: explain_memory(db, kernel, credential, hidden["memory_id"]))
            deleted = save("[T16] 即将删除的句子。")
            delete_memory(db, kernel, deleted["memory_id"], str(uuid.uuid4()))
            _, deleted_explain = _call(lambda: explain_memory(db, kernel, credential, deleted["memory_id"]))
            shapes = [_shape(item) for item in (pending_explain, rejected_explain, hidden_explain, deleted_explain)]
            t16_ok = len(set(shapes)) == 1 and shapes[0] == "404 NOT_FOUND memory not found"
            if not t16_ok:
                fail(f"T16 explain responses differ: {shapes}")
            note(
                "T16",
                "explain 各状态",
                "待确认、已拒绝、不可见、已删除返回相同的不存在",
                " / ".join(shapes),
                "通过" if t16_ok else "缺陷 S2",
                target_id=f"{hidden['memory_id']}|{deleted['memory_id']}",
            )

            _, idea, idea_error = propose(
                {"type": "add", "content": "[T17] 计划A", "kind": "fact", "category": "goal"},
                "E批 T17 2026-09-27",
            )
            _, revised, revised_error = propose(
                _update("[T17] 计划B", {"memory_id": idea["proposal_id"], "revision": 1}),
                "E批 T17 2026-09-27",
            )
            note(
                "T17",
                "未批准就想改",
                "方案希望提示先拒绝再重提；现有文案只说明找不到记忆",
                f"新增={None if idea_error else idea['status']}；再改={_err(revised_error)}",
                "记录 S3：没有下一步指引",
                proposal_id=None if idea_error else idea["proposal_id"],
            )

            batch_target = save("[T18] 批量更新的原句。")
            batch = []
            mixed = [
                ("preference", "[T18-01] 偏好甲"),
                ("goal", "[T18-02] 目标乙"),
                ("identity", "[T18-03] 身份丙"),
                ("preference", "[T18-04] 偏好丁"),
                ("project", "[T18-05] 项目戊"),
                ("goal", "[T18-06] 目标己"),
                ("identity", "[T18-07] 身份庚"),
                ("event", "[T18-08] 事件辛"),
                ("preference", "[T18-09] 偏好壬"),
                ("project", "[T18-10] 项目癸"),
            ]
            for category, content in mixed:
                kind = "event" if category == "event" else "fact"
                key, made, made_error = propose(
                    {"type": "add", "content": content, "kind": kind, "category": category},
                    f"E批 T18 {content[:7]} 2026-09-27",
                )
                stored = None if made_error else json.loads(proposal(made["proposal_id"])["payload_json"])["content"]
                batch.append({"content": content, "proposal_id": None if made_error else made["proposal_id"], "request_id": key, "stored": stored, "error": _err(made_error)})
            for index, content in enumerate(("[T18-u1] 更新一", "[T18-u2] 更新二", "[T18-u3] 更新三"), start=1):
                key, made, made_error = propose(_update(content, batch_target), f"E批 T18 u{index} 2026-09-27")
                stored = None if made_error else json.loads(proposal(made["proposal_id"])["payload_json"])["content"]
                batch.append({"content": content, "proposal_id": None if made_error else made["proposal_id"], "request_id": key, "stored": stored, "error": _err(made_error)})
            intact = len(batch) == 13 and all(item["stored"] == item["content"] and item["proposal_id"] for item in batch)
            unique_ids = len({item["proposal_id"] for item in batch}) == 13
            if not intact or not unique_ids:
                fail("T18 lost a proposal or crossed fields")
            note(
                "T18",
                "批量提交",
                "13 条都返回且正文不串",
                f"条数={len(batch)}；id 唯一={unique_ids}；逐字一致={intact}",
                "通过" if intact and unique_ids else "缺陷 S0",
                proposal_id=",".join(item["proposal_id"] or "" for item in batch),
                target_id=batch_target["memory_id"],
            )

            _, october, october_error = propose(
                {"type": "add", "content": "[T19] 目标：十月底前完成青柠目标。", "kind": "fact", "category": "goal"},
                "E批 T19 2026-09-27",
            )
            october_done, october_decide = decide(october["proposal_id"], {"decision": "accept"})
            _, november, november_error = propose(
                _update("[T19] 目标：十一月底前完成青柠目标。", october_done),
                "E批 T19 2026-09-27",
            )
            november_done, november_decide = decide(
                november["proposal_id"],
                _update_body(october_done["memory_id"], 1, "[T19] 目标：十一月底前完成青柠目标。"),
            )
            _, december, december_error = propose(
                _update("[T19] 目标：十二月底前完成青柠目标。", november_done, revision=2),
                "E批 T19 2026-09-27",
            )
            december_done, december_decide = decide(
                december["proposal_id"],
                _update_body(october_done["memory_id"], 2, "[T19] 目标：十二月底前完成青柠目标。"),
            )
            final = memory(october_done["memory_id"])
            found = hits("青柠目标")
            old_hit = hits("十月底前完成青柠目标")
            tasked = context("青柠目标的截止日期")
            december_text = "[T19] 目标：十二月底前完成青柠目标。"
            returned = found + old_hit + tasked
            current_only = (
                final["content"] == december_text
                and final["revision"] == 3
                and december_text in found
                and "[T19] 目标：十月底前完成青柠目标。" not in returned
                and "[T19] 目标：十一月底前完成青柠目标。" not in returned
            )
            if not current_only:
                fail(f"T19 returned an old version: search={found} context={tasked} memory={final}")
            note(
                "T19",
                "目标顺延三次",
                "版本递增到 3，当前正文是十二月；旧版本不作为搜索或按任务获取的结果",
                f"版本={final['revision'] if final else None}；正文={final['content'] if final else None}；搜索={found}；旧句搜索={old_hit}；按任务={tasked}",
                "通过" if current_only else "缺陷 S0",
                proposal_id=f"{october['proposal_id']}|{november['proposal_id']}|{december['proposal_id']}",
                target_id=october_done["memory_id"],
            )
            result["s0_failures"] = failures
    finally:
        RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
        RESULT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if failures:
        raise AssertionError("; ".join(failures))


def save_update(db, kernel, saved: dict, content: str) -> dict:
    return publish_memory(
        db,
        kernel,
        str(uuid.uuid4()),
        {
            "content": content,
            "kind": "fact",
            "category": "preference",
            "share_enabled": True,
            "source_refs": [],
            "target_id": saved["memory_id"],
            "base_revision": saved["revision"],
        },
    )


def _update(content: str, saved: dict, revision: int | None = None) -> dict:
    return {
        "type": "update",
        "content": content,
        "kind": "fact",
        "category": "preference",
        "target_id": saved["memory_id"],
        "base_revision": saved["revision"] if revision is None else revision,
    }


def _update_body(memory_id: str, revision: int, content: str) -> dict:
    return {"decision": "update", "target_id": memory_id, "base_revision": revision, "content": content}


def _call(func):
    try:
        return func(), None
    except ApiError as exc:
        return None, exc


def _err(exc: ApiError | None) -> str:
    if exc is None:
        return "none"
    return f"{exc.status} {exc.code} {exc.message}"


def _shape(exc: ApiError | None) -> str:
    if exc is None:
        return "returned-body"
    return f"{exc.status} {exc.code} {exc.message}"


def _overlap(left: str, right: str) -> float:
    grams_left = _grams(left)
    grams_right = _grams(right)
    if len(grams_left) < 4 or len(grams_right) < 4:
        return 0
    hit = sum(1 for gram in grams_left if gram in grams_right)
    shorter = min(len(grams_left), len(grams_right))
    contained = len(left) >= 12 and len(right) >= 12 and (left in right or right in left)
    return 1 if contained else hit / shorter


def _grams(value: str) -> set[str]:
    text = "".join(value.split())
    return {text[index : index + 2] for index in range(len(text) - 1)}
