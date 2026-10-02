"""Agent-as-extractor test round.

Each test asserts the behaviour the plan needs to go live. A failing test is
a gap, and its observations are written to results/<id>.json either way.
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

import pytest

import harness
import mcpclient
from harness import ALL_CATEGORIES, GOLD, SAMPLE, LiveServer, agent_headers, record

EXAMPLE_UUID = "123e4567-e89b-12d3-a456-426614174000"  # the RFC/docs example models often copy


@pytest.fixture
def server():
    live = LiveServer().start()
    yield live
    live.stop()


def secret_of(entry: dict) -> str:
    return entry["env"]["ZHIWO_AGENT_CREDENTIAL"]


def propose(server: LiveServer, secret: str, candidate: dict, *, rid: str | None = None, source_ref: str | None = None):
    evidence = {"text": candidate["evidence"]}
    if source_ref:
        evidence["source_ref"] = source_ref
    body = {
        "change": {"type": "add", "content": candidate["content"], "kind": candidate["kind"], "category": candidate["category"]},
        "evidence": evidence,
    }
    if rid is not None:
        body["request_id"] = rid
    return server.call("POST", "/api/v1/agent/tools/propose_memory", body, agent_headers(secret))


def pending(server: LiveServer) -> list[dict]:
    status, body = server.owner("GET", "/api/v1/proposals?status=pending")
    assert status == 200
    return body["proposals"]


def events(server: LiveServer) -> list[dict]:
    status, body = server.owner("GET", "/api/v1/access-events")
    assert status == 200
    return body["events"]


# ---------------------------------------------------------------- L1 / L3

def test_T01_one_click_preset_accepts_all_categories(server):
    """S1: one-click connect with "允许提议修改", then extract the sample."""
    entry = server.connect("propose")
    secret = secret_of(entry)
    _, agents = server.owner("GET", "/api/v1/agents")
    granted = agents["agents"][0]["allowed_categories"]
    outcomes = []
    for candidate in GOLD:
        status, body = propose(server, secret, candidate)
        outcomes.append({"category": candidate["category"], "status": status, "error": body.get("error", {}).get("code")})
    accepted = [item for item in outcomes if item["status"] == 200]
    record("T01_preset_categories", {
        "granted_categories": granted,
        "attempted": len(GOLD),
        "accepted": len(accepted),
        "rate": round(len(accepted) / len(GOLD), 2),
        "outcomes": outcomes,
        "pending_after": len(pending(server)),
    })
    assert len(accepted) == len(GOLD), f"only {len(accepted)}/{len(GOLD)} accepted; granted={granted}"


def test_T02_write_scope_needs_read_scope(server):
    """Proposing a category must not require reading it."""
    owner_fact = "身份探针：用户的真实姓名是林舟。"
    status, _ = server.owner("POST", "/api/v1/memories", {
        "content": owner_fact, "kind": "fact", "category": "identity", "share_enabled": True,
    })
    assert status == 200
    secret = secret_of(server.connect("propose"))
    _, agents = server.owner("GET", "/api/v1/agents")
    agent = agents["agents"][0]
    proposed, _ = propose(server, secret, GOLD[0])
    _, found = server.call("POST", "/api/v1/agent/tools/search_memory", {"query": "真实姓名"}, agent_headers(secret))
    leaked = [item["content"] for item in found.get("items", [])]
    record("T02_read_write_coupling", {
        "read_categories": agent["allowed_categories"],
        "propose_categories": agent.get("propose_categories"),
        "identity_proposal_status": proposed,
        "identity_visible_to_agent": owner_fact in leaked,
    })
    assert proposed == 200, "the connection cannot propose identity"
    assert owner_fact not in leaked, "proposing identity exposed identity memories to reads"


def test_T03_request_id_reuse_by_model(server):
    """Models copy example UUIDs or invent ids. The tool surface should not ask for one."""
    entry = server.connect("propose")
    secret = secret_of(entry)
    params = server.bridge_command(entry)

    async def steps(session, init):
        tools = {tool.name: tool for tool in (await session.list_tools()).tools}
        stored = []
        for candidate in GOLD[:4]:
            result = await session.call_tool("propose_memory", {"content": candidate["content"], "category": candidate["category"], "evidence": candidate["evidence"]})
            stored.append(not result.is_error and '"status":"pending"' in mcpclient.text_of(result))
        return tools, stored

    tools, stored = mcpclient.run(params, steps)
    exposes_request_id = any("request_id" in tool.input_schema.get("properties", {}) for tool in tools.values())
    # HTTP callers that do send a request id keep strict idempotency: same id, different payload -> 409.
    first, _ = propose(server, secret, GOLD[4], rid=EXAMPLE_UUID)
    second, body = propose(server, secret, GOLD[5], rid=EXAMPLE_UUID)
    record("T03_request_id", {
        "mcp_tools_expose_request_id": exposes_request_id,
        "mcp_distinct_candidates_stored": stored,
        "http_same_id_different_payload": [first, second, body.get("error", {}).get("code")],
        "pending": len(pending(server)),
    })
    assert not exposes_request_id, "MCP tools still ask the model for a request id"
    assert all(stored), "distinct candidates were dropped"


@pytest.mark.xfail(strict=True, reason="P1：导入交给 Agent 尚未实现")
def test_T04_import_then_agent_extracts(server):
    """S2: import without a model, then let the agent extract that source."""
    secret = secret_of(server.connect("propose", categories=ALL_CATEGORIES))
    status, job = server.owner("POST", "/api/v1/imports", {"kind": "paste", "text": SAMPLE})
    assert status == 200
    source_id = job["source_id"]
    status, body = propose(server, secret, GOLD[3], source_ref=source_id)
    proposal = pending(server)[0]
    _, job_after = server.owner("POST", f"/api/v1/imports/{job['job_id']}/retry")
    tool_names = sorted(json.loads((harness.SERVER / "zhiwo" / "contracts" / "memory.py").read_text(encoding="utf-8").split("TOOLS = ")[1].split("\n")[0].replace("(", "[").replace(")", "]")))
    record("T04_import_link", {
        "import_status_before": job["status"],
        "import_status_after_agent": job_after["status"],
        "agent_tools": tool_names,
        "agent_can_read_pending_source": any("source" in name for name in tool_names),
        "proposal_source_id_equals_import": proposal["source"]["id"] == source_id,
        "proposal_source_kind": proposal["source"]["kind"],
        "verification": proposal["evidence"].get("verification"),
    })
    assert proposal["source"]["id"] == source_id, "agent proposal is not attached to the imported source"
    assert proposal["evidence"].get("verification") == "matched", "exact quote from the imported source is still unverified"
    assert job_after["status"] == "extracted", "import job still waits for a model after the agent extracted it"


def test_T05_duplicate_extraction(server):
    """S3: the same text extracted twice (agent retried, or user asked again)."""
    secret = secret_of(server.connect("propose", categories=ALL_CATEGORIES))
    for _ in range(2):
        for candidate in GOLD:
            propose(server, secret, candidate)
    items = pending(server)
    contents = [item["payload"]["content"] for item in items]
    record("T05_duplicates", {"submitted": 2 * len(GOLD), "pending": len(items), "distinct": len(set(contents))})
    assert len(items) == len(set(contents)), "the same candidate sits in review twice"


@pytest.mark.xfail(strict=True, reason="P1：证据核实与审核页显示尚未实现")
def test_T06_evidence_verification_signal(server):
    """Exact quote vs paraphrase should not look the same to the owner."""
    secret = secret_of(server.connect("propose", categories=ALL_CATEGORIES))
    _, job = server.owner("POST", "/api/v1/imports", {"kind": "paste", "text": SAMPLE})
    exact = dict(GOLD[2])
    invented = dict(GOLD[2], content="今年目标：做到一万个付费用户", evidence="今年要做到一万个付费用户")
    propose(server, secret, exact, source_ref=job["source_id"])
    propose(server, secret, invented, source_ref=job["source_id"])
    marks = [item["evidence"].get("verification") for item in pending(server)]
    review_tsx = (harness.REPO / "apps" / "web" / "src" / "Review.tsx").read_text(encoding="utf-8")
    record("T06_verification", {"exact_vs_invented": marks, "review_page_reads_verification": "verification" in review_tsx})
    assert marks[0] != marks[1], "a quote that is not in the source gets the same mark as an exact quote"
    assert "verification" in review_tsx, "review page does not show whether evidence was checked"


def _bad_requests():
    good = GOLD[3]
    base = {"type": "add", "content": good["content"], "kind": good["kind"], "category": good["category"]}
    return {
        "chinese_category": ({**base, "category": "偏好"}, "category"),
        "missing_kind": ({key: value for key, value in base.items() if key != "kind"}, "kind"),
        "extra_field": ({**base, "confidence": 0.9}, "confidence"),
        "too_long": ({**base, "content": "长" * 2100}, "content"),
    }


def test_T07_error_messages(server):
    """Wrong field values must say which field and which values are allowed."""
    secret = secret_of(server.connect("propose"))
    seen = {}
    for name, (change, field) in _bad_requests().items():
        status, body = server.call("POST", "/api/v1/agent/tools/propose_memory", {"change": change, "evidence": {"text": GOLD[3]["evidence"]}}, agent_headers(secret))
        seen[name] = {"status": status, "field": field, **body.get("error", {})}
    record("T07_errors", {"cases": seen})
    unclear = [name for name, item in seen.items() if item["field"] not in (item.get("message") or "")]
    assert not unclear, f"errors that do not name the field: {unclear}"
    assert "identity" in seen["chinese_category"]["message"], "category error does not list the allowed values"


@pytest.mark.xfail(strict=True, reason="P1：字段校验失败尚未写入访问记录")
def test_T07b_rejections_are_audited(server):
    secret = secret_of(server.connect("propose"))
    before = len(events(server))
    for change, _ in _bad_requests().values():
        server.call("POST", "/api/v1/agent/tools/propose_memory", {"change": change, "evidence": {"text": GOLD[3]["evidence"]}}, agent_headers(secret))
    assert len(events(server)) - before == len(_bad_requests())


def test_T08_review_and_explain_roundtrip(server):
    """S4: owner accepts an agent proposal; agent can explain it later."""
    secret = secret_of(server.connect("propose", categories=ALL_CATEGORIES))
    propose(server, secret, GOLD[4])
    proposal = pending(server)[0]
    status, decided = server.call("POST", f"/api/v1/proposals/{proposal['id']}/decision", {"decision": "accept"}, harness.owner_headers())
    memory_id = decided.get("memory_id")
    status2, explained = server.call("POST", "/api/v1/agent/tools/explain_memory", {"id": memory_id}, agent_headers(secret))
    record("T08_roundtrip", {"decision_status": status, "memory_id": memory_id, "explain_status": status2, "explain": explained})
    assert status == 200 and status2 == 200
    assert explained["result"]["evidence"] == GOLD[4]["evidence"]


def test_T09_update_flow(server):
    """S5: agent finds an existing memory and proposes an update."""
    status, created = server.owner("POST", "/api/v1/memories", {"content": "在做「青柠账本」项目，用 React Native 开发", "kind": "fact", "category": "project"})
    secret = secret_of(server.connect("propose", categories=ALL_CATEGORIES))
    _, found = server.call("POST", "/api/v1/agent/tools/search_memory", {"query": "青柠账本 开发"}, agent_headers(secret))
    hit = found["items"][0]
    body = {
        "request_id": str(uuid.uuid4()),
        "change": {"type": "update", "content": GOLD[4]["content"], "kind": "fact", "category": "project", "target_id": hit["id"], "base_revision": hit["revision"]},
        "evidence": {"text": GOLD[4]["evidence"]},
    }
    s1, _ = server.call("POST", "/api/v1/agent/tools/propose_memory", body, agent_headers(secret))
    proposal = pending(server)[0]
    s2, decided = server.call("POST", f"/api/v1/proposals/{proposal['id']}/decision",
                              {"decision": "update", "target_id": hit["id"], "base_revision": hit["revision"], "content": GOLD[4]["content"]},
                              harness.owner_headers())
    record("T09_update", {"propose": s1, "decide": s2, "revision_after": decided.get("revision")})
    assert s1 == 200 and s2 == 200 and decided.get("revision") == hit["revision"] + 1


# ---------------------------------------------------------------- L2 / L4 via the real bridge

VARIANTS = {
    "baseline": lambda c: {"content": c["content"], "category": c["category"], "evidence": c["evidence"]},
    "category_in_chinese": lambda c: {"content": c["content"], "category": "偏好", "evidence": c["evidence"]},
    "kind_in_chinese": lambda c: {"content": c["content"], "category": c["category"], "evidence": c["evidence"], "kind": "事实"},
    "extra_confidence_field": lambda c: {"content": c["content"], "category": c["category"], "evidence": c["evidence"], "confidence": 0.9},
    "evidence_as_object": lambda c: {"content": c["content"], "category": c["category"], "evidence": {"text": c["evidence"]}},
    "missing_evidence": lambda c: {"content": c["content"], "category": c["category"]},
    "too_long": lambda c: {"content": "长" * 2100, "category": c["category"], "evidence": c["evidence"]},
    "identity_under_preset": lambda c: {"content": "用户叫林舟", "category": "identity", "evidence": "我叫林舟"},
    "same_as_baseline": lambda c: {"content": c["content"], "category": c["category"], "evidence": c["evidence"]},
}


def test_T10_mcp_surface_and_fault_injection(server):
    entry = server.connect("propose")
    params = server.bridge_command(entry)
    candidate = GOLD[3]

    async def steps(session, init):
        tools = {tool.name: tool for tool in (await session.list_tools()).tools}
        results = {}
        for name, build in VARIANTS.items():
            result = await session.call_tool("propose_memory", build(candidate))
            text = mcpclient.text_of(result)
            results[name] = {"is_error": bool(result.is_error), "stored": '"status":"pending"' in text, "text": text[:240]}
        return init.instructions, tools, results

    instructions, tools, results = mcpclient.run(params, steps)
    schema = tools["propose_memory"].input_schema
    silent = [name for name, item in results.items() if not item["stored"] and not item["is_error"]]
    record("T10_mcp", {
        "instructions": instructions,
        "propose_description": tools["propose_memory"].description,
        "propose_schema": schema,
        "variants": results,
        "failures_not_flagged_as_error": silent,
        "pending": len(pending(server)),
    })
    assert schema["properties"]["category"].get("enum") == ALL_CATEGORIES
    assert not silent, f"failed calls returned as normal results: {silent}"
    assert results["identity_under_preset"]["stored"], "one-click preset still cannot propose identity"
    assert results["same_as_baseline"]["text"] == results["baseline"]["text"], "a repeated suggestion was stored twice"
    assert "category" in results["category_in_chinese"]["text"] and "identity" in results["category_in_chinese"]["text"]
