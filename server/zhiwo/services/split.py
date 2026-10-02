"""Split a Markdown note into candidates without a model, then dedupe.

Used when no extraction model is configured. A list item, or a plain line,
becomes one candidate. The nearest heading picks the category; when the
heading says nothing useful, a few phrases in the line do. The evidence is
the original line, so it is always verbatim source text. Nothing here
publishes a memory.

`dedupe` runs on every import, model or not: candidates whose text is the
same after normalizing spaces, full/half-width punctuation and trailing
punctuation are merged, and one that is already a current memory is dropped.
Near matches stay as candidates, marked `similar_to`, for the owner to pick.
"""

from __future__ import annotations

import re
import unicodedata

SIMILAR_THRESHOLD = 0.62

# Longest matching phrase wins, so "working agreements" beats "work".
_HEADING_WORDS: dict[str, tuple[str, ...]] = {
    "identity": ("关于我", "我是谁", "个人信息", "基本信息", "个人背景", "背景", "身份", "自我介绍", "about me", "about", "who i am", "profile", "background", "bio", "personal"),
    "preference": ("偏好", "习惯", "风格", "规范", "约定", "规则", "沟通", "回答", "输出", "代码", "编码", "写作", "格式", "工具", "协作", "preference", "style", "convention", "rule", "guideline", "workflow", "communication", "working agreement", "tone", "format", "coding", "code", "tooling"),
    "project": ("项目", "在做", "正在做", "手头", "当前工作", "current work", "working on", "project", "work"),
    "goal": ("目标", "计划", "愿望", "长期", "goal", "objective", "plan", "aspiration"),
    "event": ("事件", "经历", "近况", "最近发生", "event", "history", "timeline", "changelog"),
    "other": ("其他", "杂项", "备注", "misc", "other", "notes"),
}

_CONTENT_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("identity", re.compile(r"^(我是|我叫|我的名字|i am |i'm |my name)|(职业|所在城市|住在|坐标)[：:]|(工程师|设计师|开发者|开发工程师|老师|教师|经理|分析师|研究员|学生|医生|律师)[，,。\s]|^(engineer|designer|developer|student)\b", re.I)),
    ("goal", re.compile(r"希望|想学|想要|打算|目标|梦想|年底前|今年|明年|三年内|五年内|\b(want to|plan to|goal|this year|next year)\b", re.I)),
    ("project", re.compile(r"正在|在做|负责|维护|迁移|迁到|迁回|上线|交付|这学期|本季度|\b(working on|migrating|building|maintaining)\b", re.I)),
    ("event", re.compile(r"上周|上个月|昨天|去年|已经完成|完成了|\b(last week|yesterday|last year)\b", re.I)),
    ("preference", re.compile(r"喜欢|偏好|习惯|不用|不要|别用|别再|请|默认|统一用|优先|先.+再|要用|要有|少用|多用|尽量|避免|\b(prefer|always|never|use|don't|avoid|keep)\b", re.I)),
)

_FENCE = re.compile(r"^\s*(```|~~~)")
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_LIST = re.compile(r"^(\s*)(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?(.*)$")
_RULE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
_EMPHASIS = re.compile(r"(\*\*|__|\*|_|`)(.+?)\1")
_LABEL_ONLY = re.compile(r"^[^。.!！?？]{1,24}[：:]$")
_TRAILING = "。．.!！?？;；,，、 "


def looks_structured(text: str) -> bool:
    """True when the text has Markdown headings or list items."""
    return any(_HEADING.match(line) or _LIST.match(line) for line in text.splitlines())


def split_markdown(text: str) -> list[dict]:
    candidates: list[dict] = []
    headings: list[tuple[int, str]] = []
    labels: list[tuple[int, str]] = []
    in_fence = False
    in_front = False
    for number, raw in enumerate(text.splitlines()):
        line = raw.rstrip()
        if number == 0 and line.strip() == "---":
            in_front = True
            continue
        if in_front:
            if line.strip() in {"---", "..."}:
                in_front = False
            continue
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence or not line.strip():
            continue
        heading = _HEADING.match(line)
        if heading:
            level = len(heading.group(1))
            headings = [item for item in headings if item[0] < level] + [(level, _plain(heading.group(2)))]
            labels = []
            continue
        stripped = line.strip()
        if _RULE.match(line) or stripped.startswith(("|", "<!--", "<")):
            continue
        listed = _LIST.match(line)
        if listed:
            indent = len(listed.group(1).expandtabs(4))
            body = listed.group(2).strip()
        else:
            indent = -1
            body = stripped.lstrip("> ").strip()
        content = _plain(body)
        labels = [item for item in labels if item[0] < indent]
        if not content or len(content) < 2:
            continue
        if _LABEL_ONLY.match(content):
            if listed:
                labels.append((indent, content.rstrip("：:").strip()))
            else:
                headings = [item for item in headings if item[0] < 7] + [(7, content.rstrip("：:").strip())]
            continue
        if labels and listed:
            content = f"{labels[-1][1]}：{content}"
        evidence = body if body in text else stripped
        category = _category(headings, content)
        candidates.append(
            {
                "content": content,
                "kind": "event" if category == "event" else "fact",
                "category": category,
                "scope": None,
                "evidence": evidence,
            }
        )
    return candidates


def dedupe(candidates: list[dict], existing: list[dict]) -> tuple[list[dict], int]:
    """Merge identical candidates, drop ones already remembered, mark near ones.

    `existing` holds current memories as {"id", "content"}. Returns the kept
    candidates and how many were merged or dropped as duplicates.
    """
    remembered = {normalize(item["content"]): item for item in existing if isinstance(item.get("content"), str)}
    kept: list[dict] = []
    seen: dict[str, dict] = {}
    merged = 0
    for candidate in candidates:
        key = normalize(candidate["content"])
        if not key:
            continue
        if key in remembered:
            merged += 1
            continue
        if key in seen:
            seen[key]["merged"] = seen[key].get("merged", 0) + 1
            merged += 1
            continue
        similar = _similar(candidate["content"], existing, kept)
        if similar is not None:
            candidate = {**candidate, "similar_to": similar}
        seen[key] = candidate
        kept.append(candidate)
    return kept, merged


def normalize(text: str) -> str:
    folded = unicodedata.normalize("NFKC", _plain(text)).lower()
    folded = " ".join(folded.split())
    return folded.rstrip(_TRAILING).strip()


def overlap(left: str, right: str) -> float:
    """Character-bigram overlap, the same rule the review page has used since v1."""
    grams_left = _grams(left)
    grams_right = _grams(right)
    if len(grams_left) < 4 or len(grams_right) < 4:
        return 0.0
    hit = sum(1 for gram in grams_left if gram in grams_right)
    contained = len(left) >= 12 and len(right) >= 12 and (left in right or right in left)
    return 1.0 if contained else hit / min(len(grams_left), len(grams_right))


def _similar(content: str, existing: list[dict], kept: list[dict]) -> dict | None:
    best: tuple[float, dict] | None = None
    for item in existing:
        value = item.get("content") or ""
        if len(value) < 8:
            continue
        score = overlap(content, value)
        if score >= SIMILAR_THRESHOLD and (best is None or score > best[0]):
            best = (score, {"memory_id": item["id"], "content": value})
    for item in kept:
        value = item["content"]
        if len(value) < 8:
            continue
        score = overlap(content, value)
        if score >= SIMILAR_THRESHOLD and (best is None or score > best[0]):
            best = (score, {"content": value})
    return None if best is None else best[1]


def _category(headings: list[tuple[int, str]], content: str) -> str:
    for _level, title in reversed(headings):
        found = _heading_category(title)
        if found and found != "other":
            return found
        if found == "other":
            break
    for category, pattern in _CONTENT_RULES:
        if pattern.search(content):
            return category
    return "other"


def _heading_category(title: str) -> str | None:
    lowered = title.lower()
    best: tuple[int, str] | None = None
    for category, words in _HEADING_WORDS.items():
        for word in words:
            if word in lowered and (best is None or len(word) > best[0]):
                best = (len(word), category)
    return None if best is None else best[1]


def _plain(text: str) -> str:
    previous = None
    value = text.strip()
    while previous != value:
        previous = value
        value = _EMPHASIS.sub(r"\2", value)
    return value.strip()


def _grams(value: str) -> set[str]:
    text = "".join(value.split())
    return {text[index : index + 2] for index in range(len(text) - 1)}
