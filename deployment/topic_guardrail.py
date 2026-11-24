#!/usr/bin/env python3
"""Two-layer selective topic guardrail for chat-digest summaries (MIT License)."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

GUARDRAIL_VERSION = "topic-guardrail-v1"
NEUTRAL_EMPTY_ONE_LINE = "此时间窗口内以零散日常交流为主，暂无集中公开议题。"

GUARDRAIL_SCHEMA = {
    "type": "object",
    "properties": {
        "drop_topic_indices": {"type": "array", "items": {"type": "integer"}},
        "strip_message_ids": {"type": "array", "items": {"type": "string"}},
        "drop_quote_indices": {"type": "array", "items": {"type": "integer"}},
        "one_line_unsafe": {"type": "boolean"},
        "replacement_one_line": {"type": "string"},
    },
    "required": [
        "drop_topic_indices",
        "strip_message_ids",
        "drop_quote_indices",
        "one_line_unsafe",
        "replacement_one_line",
    ],
    "additionalProperties": False,
}

GUARDRAIL_SYSTEM_PROMPT = (
    "你是一个面向中国大陆微信群与 QQ 群聊简报的出站内容合规审核器。"
    "你的职责是检查已经生成的群聊总结（one_line、topics、quotes）及其引用的原始发言摘要（cited_messages），"
    "识别并静默剔除应当选择性忽视的话题、引用消息和金句，确保最终输出适合在微信与 QQ 群正常展示。\n\n"
    "【必须剔除的内容】\n"
    "1. 中国政治与敏感时政：中国政治人物与党政官员（含现任/卸任领导人、地方官员、落马/被查/双开/逮捕/处分通报与人事变动议论）、"
    "党政体制或方针政策评价与讽刺、敏感历史或政治事件、涉台/涉港/涉疆/涉藏分裂或敏感外交争议、以及复述或议论涉政新闻、键政吐槽、政治暗喻与官场八卦。\n"
    "2. 平台封号与违法违规红线：分享或推广翻墙机场/VPN/代理节点订阅链接与翻墙教程、黄赌毒与黑灰产（洗钱跑分、买卖银行卡/账号、作弊代考）、"
    "人肉开盒/社工库查询/泄露真实身份证号手机号或密钥 Token、极端暴力与自残引导。\n\n"
    "【必须放行、绝不误伤的正常内容】\n"
    "1. 高校正常课程与日常学习：提及“马原”“毛概”“习概”“思政”“近现代史纲要”等课程名称、作业、点名、考试复习吐槽（不含体制评价或争议时政）。\n"
    "2. 正常计算机/网络安全/AI 工程交流：讨论反向代理（Nginx/Caddy）、API 网关与中转、OpenClash/Tailscale 路由与 DNS 排障、"
    "海外 AI 工具限额或账号风控吐槽、Linux/数据库/内核调试、CTF 与安全实验（只要不推广翻墙机场或分享代理节点订阅）。\n"
    "3. 正常校园生活、社团活动、摄影/硬件/3D打印讨论、二次元与游戏剧情、普通玩笑与日常吐槽。\n\n"
    "【输出字段规则】\n"
    "只返回符合 JSON Schema 的单个对象：\n"
    "- drop_topic_indices: 需要整段静默删除的 topic_index 整数数组（话题主题、summary 或 details 涉及上述需剔除内容时填入）。\n"
    "- strip_message_ids: 仅在某个正常话题本身安全、但 cited_messages 中混入了少量需剔除消息时，填入那些消息的 reference 字符串，将其从引用中剥离。\n"
    "- drop_quote_indices: 需要静默删除的金句对应的 quote_index 整数数组（quote_index 必须严格取输入 quotes 里的 quote_index 值）。\n"
    "- one_line_unsafe: 若原 one_line 提及了被删除话题/敏感内容，或包含“已略过敏感内容”等元提示语，则为 true，否则 false。\n"
    "- replacement_one_line: 当 one_line_unsafe 为 true 或删去了部分话题时，只根据保留下来的安全话题重写一两句客观平实的概括；"
    "若保留话题为空则输出空字符串。严禁在 replacement_one_line 中提及“已略过/过滤/忽略敏感内容”。"
)

_ID_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_ID_CHECK_CHARS = "10X98765432"
_ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff\u00ad]")
_COMPACT_STRIP_RE = re.compile(
    r"[\s`~!@#$%^&*()\-_=+\[\]{}\\|;:'\",<.>/?·•—…，。！？、；：“”‘’（）【】《》]+"
)
_CN_ID_RE = re.compile(
    r"(?<![0-9A-Za-z])([1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx])(?![0-9A-Za-z])"
)

_HARD_REDLINE_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "circumvention_uri",
        re.compile(
            r"(?:vmess|vless|trojan|ssr|hy2|hysteria2|tuic)://[^\s]{8,}|ss://[A-Za-z0-9+/=_-]{12,}",
            re.IGNORECASE,
        ),
    ),
    (
        "circumvention_promo",
        re.compile(
            r"(?:机场|梯子|翻墙|科学上网)\s*(?:推荐|订阅|节点|购买|合租|教程|分享)"
            r"|免费(?:机场|梯子|节点)订阅"
            r"|(?:分享|送|发个)\s*(?:机场|梯子)?\s*订阅链接",
            re.IGNORECASE,
        ),
    ),
    (
        "private_key",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----"),
    ),
    (
        "api_token",
        re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b|\bBearer\s+[A-Za-z0-9._~+/=-]{16,}", re.IGNORECASE),
    ),
    (
        "contraband_or_doxxing",
        re.compile(
            r"(?:人肉|恶意|网上|挂人|查户籍|威胁|遭到?|被|去|帮|找人|求).{0,6}开盒"
            r"|开盒.{0,8}(?:他人|别人|同学|群友|身份证|户籍|住址|手机号|隐私|挂人|教程|群)"
            r"|社工库(?:查档|查询|地址|机器人)?"
            r"|查开房|买卖银行卡|银行卡四件套|四件套(?:出售|收购)|跑分洗钱",
            re.IGNORECASE,
        ),
    ),
)

_META_FILTER_NOTICE_RE = re.compile(
    r"(?:敏感|涉政|政治|违规|不宜展示|受限)(?:话题|内容|消息|讨论|言论)?\s*(?:已|予以|被|均已|将)?\s*"
    r"(?:略过|忽略|跳过|过滤|屏蔽|删除|剔除|隐藏|不予展示|未予收录|未收录)"
    r"|(?:已|已经)\s*(?:略过|忽略|跳过|过滤|屏蔽|剔除)\s*(?:部分|相关|上述|以下)?\s*(?:敏感|涉政|违规|政治)"
    r"|不说这个|不便展示相关内容|敏感内容暂不展示",
    re.IGNORECASE,
)


def normalize_basic(text: str) -> str:
    """Normalize Unicode width, strip zero-width chars, collapse spaces, and casefold."""
    cleaned = _ZERO_WIDTH_RE.sub("", unicodedata.normalize("NFKC", str(text or "")))
    return re.sub(r"\s+", " ", cleaned).strip().casefold()


def normalize_compact(text: str) -> str:
    """Strip spaces and punctuation separators to resist simple obfuscation."""
    return _COMPACT_STRIP_RE.sub("", normalize_basic(text))


def is_valid_cn_id_card(candidate: str) -> bool:
    """Validate an 18-digit Chinese resident ID number using date and ISO 7064 Mod 11-2."""
    code = str(candidate or "").strip().upper()
    if len(code) != 18 or not code[:17].isdigit():
        return False
    year = int(code[6:10])
    month = int(code[10:12])
    day = int(code[12:14])
    if not (1900 <= year <= 2099):
        return False
    try:
        dt.date(year, month, day)
    except ValueError:
        return False
    total = sum(int(code[index]) * _ID_WEIGHTS[index] for index in range(17))
    return code[17] == _ID_CHECK_CHARS[total % 11]


def has_meta_filter_notice(text: str) -> bool:
    """Detect meta-phrases that disclose content was skipped or filtered."""
    basic = normalize_basic(text)
    return bool(basic and _META_FILTER_NOTICE_RE.search(basic))


def check_hard_redlines(text: str, extra_terms: Iterable[str] = ()) -> str | None:
    """Return a redline tag if deterministic rules match, else None."""
    raw = str(text or "")
    if not raw.strip():
        return None
    basic = normalize_basic(raw)
    compact = normalize_compact(raw)
    for tag, pattern in _HARD_REDLINE_RULES:
        if pattern.search(raw) or pattern.search(basic):
            return tag
    for match in _CN_ID_RE.finditer(raw):
        if is_valid_cn_id_card(match.group(1)):
            return "cn_id_card"
    for term in extra_terms:
        cleaned = str(term or "").strip()
        if not cleaned:
            continue
        term_compact = normalize_compact(cleaned)
        if len(term_compact) >= 2 and term_compact in compact:
            return "custom_block_term"
        term_basic = normalize_basic(cleaned)
        if term_basic and term_basic in basic:
            return "custom_block_term"
    return None


def _resolve_blocklist_path(raw_path: str, runtime: Path | None) -> Path:
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute() and runtime is not None:
        candidate = runtime / candidate
    return candidate


def _read_blocklist_file(path: Path, *, required: bool) -> tuple[str, ...]:
    if path.is_symlink():
        raise ValueError(f"guardrail blocklist must not be a symlink: {path}")
    if not path.exists():
        if required:
            raise ValueError(f"guardrail blocklist file not found: {path}")
        return ()
    if not path.is_file():
        raise ValueError(f"guardrail blocklist path is not a regular file: {path}")
    terms: list[str] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped in seen:
            continue
        seen.add(stripped)
        terms.append(stripped)
    return tuple(terms)


def load_guardrail_settings(config: Mapping[str, Any] | None, runtime: Path | str | None) -> dict[str, Any]:
    """Load optional guardrail configuration and local private blocklist terms."""
    runtime_path = Path(runtime) if runtime is not None else None
    section = config.get("guardrail") if isinstance(config, Mapping) else None
    if section is not None and not isinstance(section, Mapping):
        raise ValueError("guardrail configuration must be an object")
    section = section or {}
    enabled = bool(section.get("enabled", True))
    semantic_review = bool(section.get("semantic_review", True))

    extra_terms: list[str] = []
    seen_terms: set[str] = set()
    raw_terms = section.get("extra_block_terms", [])
    if not isinstance(raw_terms, list) or any(not isinstance(item, str) for item in raw_terms):
        raise ValueError("guardrail.extra_block_terms must be an array of strings")
    for term in raw_terms:
        cleaned = term.strip()
        if cleaned and cleaned not in seen_terms:
            seen_terms.add(cleaned)
            extra_terms.append(cleaned)

    env_path = os.environ.get("CHAT_DIGEST_GUARDRAIL_BLOCKLIST", "").strip()
    configured_path = section.get("blocklist_file")
    if configured_path is not None and (not isinstance(configured_path, str) or not configured_path.strip()):
        raise ValueError("guardrail.blocklist_file must be a non-empty path string")

    file_terms: tuple[str, ...] = ()
    if env_path:
        file_terms = _read_blocklist_file(_resolve_blocklist_path(env_path, runtime_path), required=True)
    elif configured_path:
        file_terms = _read_blocklist_file(_resolve_blocklist_path(configured_path.strip(), runtime_path), required=True)
    elif runtime_path is not None:
        file_terms = _read_blocklist_file(runtime_path / "guardrail-blocklist.txt", required=False)

    for term in file_terms:
        if term not in seen_terms:
            seen_terms.add(term)
            extra_terms.append(term)

    return {
        "enabled": enabled,
        "semantic_review": enabled and semantic_review,
        "extra_terms": tuple(extra_terms),
    }


def guardrail_cache_token(config: Mapping[str, Any] | None, runtime: Path | str | None) -> dict[str, Any]:
    """Return a deterministic fingerprint of guardrail settings for summary cache invalidation."""
    settings_cfg = load_guardrail_settings(config, runtime)
    terms_hash = hashlib.sha256(
        json.dumps(list(settings_cfg["extra_terms"]), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return {
        "version": GUARDRAIL_VERSION,
        "enabled": settings_cfg["enabled"],
        "semantic_review": settings_cfg["semantic_review"],
        "terms_sha256": terms_hash,
    }


def _iter_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _iter_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_strings(item)


def _message_texts(message: Mapping[str, Any]) -> list[str]:
    texts: list[str] = []
    for key in ("text", "reply_to", "image_understanding", "auxiliary_context"):
        if key in message:
            texts.extend(item for item in _iter_strings(message[key]) if item.strip())
    return texts


def _truncate(text: str, limit: int = 240) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"


def _compact_cited_messages(message_ids: list[str], by_ref: Mapping[str, Mapping[str, Any]], limit: int = 6) -> list[dict[str, Any]]:
    unique_ids = list(dict.fromkeys(mid for mid in message_ids if mid in by_ref))
    if len(unique_ids) > limit:
        head = unique_ids[:2]
        tail = unique_ids[-2:]
        middle_pool = [mid for mid in unique_ids[2:-2]]
        middle_pool.sort(key=lambda mid: len(str(by_ref[mid].get("text") or "")), reverse=True)
        picked = set(head + tail + middle_pool[: max(0, limit - len(head) - len(tail))])
        selected = [mid for mid in unique_ids if mid in picked][:limit]
    else:
        selected = unique_ids

    items: list[dict[str, Any]] = []
    for mid in selected:
        msg = by_ref[mid]
        row: dict[str, Any] = {
            "reference": mid,
            "author": str(msg.get("author") or ""),
            "text": _truncate(str(msg.get("text") or "")),
        }
        image = msg.get("image_understanding")
        if isinstance(image, Mapping):
            desc = _truncate(str(image.get("description") or ""), 180)
            vis = _truncate(str(image.get("visible_text") or ""), 180)
            if desc:
                row["image_description"] = desc
            if vis:
                row["image_visible_text"] = vis
        items.append(row)
    return items


def _is_model_declined_error(exc: BaseException) -> bool:
    return isinstance(exc, (ValueError, RuntimeError)) and "Model declined" in str(exc)


def _normalize_verdict(raw_verdict: Any) -> dict[str, Any]:
    if not isinstance(raw_verdict, Mapping):
        raise ValueError("guardrail verdict must be an object")
    drop_topics = [idx for idx in (raw_verdict.get("drop_topic_indices") or []) if type(idx) is int]
    strip_ids = [mid for mid in (raw_verdict.get("strip_message_ids") or []) if isinstance(mid, str) and mid]
    drop_quotes = [idx for idx in (raw_verdict.get("drop_quote_indices") or []) if type(idx) is int]
    one_line_unsafe = bool(raw_verdict.get("one_line_unsafe", False))
    replacement = str(raw_verdict.get("replacement_one_line") or "").strip()
    return {
        "drop_topic_indices": drop_topics,
        "strip_message_ids": strip_ids,
        "drop_quote_indices": drop_quotes,
        "one_line_unsafe": one_line_unsafe,
        "replacement_one_line": replacement,
    }


def _call_review(
    request_fn: Callable[..., dict[str, Any]],
    config: Mapping[str, Any] | None,
    payload: dict[str, Any],
    diagnostics_dir: Path | None,
) -> dict[str, Any]:
    raw_verdict = request_fn(
        config,
        GUARDRAIL_SCHEMA,
        GUARDRAIL_SYSTEM_PROMPT,
        payload,
        diagnostics_dir,
        "chat_group_safety_review",
    )
    return _normalize_verdict(raw_verdict)


def _bisect_declined_review(
    request_fn: Callable[..., dict[str, Any]],
    config: Mapping[str, Any] | None,
    diagnostics_dir: Path | None,
    *,
    one_line: str,
    topic_entries: list[dict[str, Any]],
    quote_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    drop_topics: set[int] = set()
    strip_ids: set[str] = set()
    drop_quotes: set[int] = set()

    def probe_topics(start: int, end: int) -> None:
        if start >= end:
            return
        chunk = topic_entries[start:end]
        reindexed = [{**item, "topic_index": pos} for pos, item in enumerate(chunk)]
        payload = {"one_line": "", "topics": reindexed, "quotes": []}
        try:
            verdict = _call_review(request_fn, config, payload, diagnostics_dir)
            for pos in verdict["drop_topic_indices"]:
                if 0 <= pos < len(chunk):
                    drop_topics.add(start + pos)
            strip_ids.update(verdict["strip_message_ids"])
        except Exception as exc:
            if not _is_model_declined_error(exc):
                raise
            if end - start == 1:
                drop_topics.add(start)
                return
            mid = (start + end) // 2
            probe_topics(start, mid)
            probe_topics(mid, end)

    def probe_quotes(start: int, end: int) -> None:
        if start >= end:
            return
        chunk = quote_entries[start:end]
        payload = {"one_line": "", "topics": [], "quotes": chunk}
        try:
            verdict = _call_review(request_fn, config, payload, diagnostics_dir)
            drop_quotes.update(verdict["drop_quote_indices"])
        except Exception as exc:
            if not _is_model_declined_error(exc):
                raise
            if end - start == 1:
                drop_quotes.add(chunk[0]["quote_index"])
                return
            mid = (start + end) // 2
            probe_quotes(start, mid)
            probe_quotes(mid, end)

    probe_topics(0, len(topic_entries))
    probe_quotes(0, len(quote_entries))

    one_line_unsafe = bool(drop_topics)
    replacement = ""
    if not one_line_unsafe and one_line.strip():
        try:
            verdict = _call_review(
                request_fn,
                config,
                {"one_line": one_line, "topics": [], "quotes": []},
                diagnostics_dir,
            )
            one_line_unsafe = verdict["one_line_unsafe"]
            replacement = verdict["replacement_one_line"]
        except Exception as exc:
            if not _is_model_declined_error(exc):
                raise
            one_line_unsafe = True

    return {
        "drop_topic_indices": sorted(drop_topics),
        "strip_message_ids": sorted(strip_ids),
        "drop_quote_indices": sorted(drop_quotes),
        "one_line_unsafe": one_line_unsafe,
        "replacement_one_line": replacement,
    }


def apply_guardrail(
    raw: Mapping[str, Any],
    *,
    candidates: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    config: Mapping[str, Any] | None,
    runtime: Path | str | None,
    diagnostics_dir: Path | None,
    request_fn: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    """Run deterministic redlines and optional batch LLM review on a generated summary."""
    settings_cfg = load_guardrail_settings(config, runtime)
    orig_one_line = str(raw.get("one_line") or "")
    orig_topics = [dict(topic) for topic in (raw.get("topics") or []) if isinstance(topic, Mapping)]
    orig_quotes = list(raw.get("quotes") or [])

    if not settings_cfg["enabled"]:
        return {"one_line": orig_one_line, "topics": orig_topics, "quotes": orig_quotes}

    extra_terms = settings_cfg["extra_terms"]
    by_ref = {
        msg["reference"]: msg
        for msg in messages
        if isinstance(msg, Mapping) and isinstance(msg.get("reference"), str)
    }

    # Stage 2A: deterministic check on cited messages, topic text, quotes, and one_line.
    stripped_refs: set[str] = set()
    checked_refs: set[str] = set()
    for topic in orig_topics:
        for mid in topic.get("message_ids") or []:
            if not isinstance(mid, str) or mid in checked_refs:
                continue
            checked_refs.add(mid)
            msg = by_ref.get(mid)
            if msg is not None and any(check_hard_redlines(text, extra_terms) for text in _message_texts(msg)):
                stripped_refs.add(mid)

    surviving_topics: list[dict[str, Any]] = []
    for topic in orig_topics:
        title = str(topic.get("title") or "")
        summary_text = str(topic.get("summary") or "")
        details = [str(item) for item in (topic.get("details") or [])]
        kept_ids = [mid for mid in (topic.get("message_ids") or []) if isinstance(mid, str) and mid not in stripped_refs]
        topic_texts = [title, summary_text, *details]
        if not kept_ids or any(
            check_hard_redlines(text, extra_terms) or has_meta_filter_notice(text)
            for text in topic_texts
        ):
            continue
        surviving_topics.append({**topic, "details": details, "message_ids": kept_ids})

    surviving_quotes: list[int] = []
    for q_idx in orig_quotes:
        if type(q_idx) is not int or not 0 <= q_idx < len(candidates):
            surviving_quotes.append(q_idx)
            continue
        candidate = candidates[q_idx]
        q_ref = str(candidate.get("message_id") or "")
        q_text = str(candidate.get("text") or "")
        if q_ref in stripped_refs or check_hard_redlines(q_text, extra_terms) or has_meta_filter_notice(q_text):
            continue
        surviving_quotes.append(q_idx)

    one_line_tainted = bool(
        check_hard_redlines(orig_one_line, extra_terms) or has_meta_filter_notice(orig_one_line)
    )

    # Stage 2B: semantic LLM compliance review.
    verdict = {
        "drop_topic_indices": [],
        "strip_message_ids": [],
        "drop_quote_indices": [],
        "one_line_unsafe": False,
        "replacement_one_line": "",
    }
    if settings_cfg["semantic_review"] and (surviving_topics or surviving_quotes or (orig_one_line.strip() and not one_line_tainted)):
        topic_entries = [
            {
                "topic_index": pos,
                "title": str(topic.get("title") or ""),
                "summary": str(topic.get("summary") or ""),
                "details": list(topic.get("details") or []),
                "cited_messages": _compact_cited_messages(topic["message_ids"], by_ref),
            }
            for pos, topic in enumerate(surviving_topics)
        ]
        quote_entries = [
            {"quote_index": q_idx, "text": str(candidates[q_idx].get("text") or "")}
            for q_idx in surviving_quotes
            if type(q_idx) is int and 0 <= q_idx < len(candidates)
        ]
        review_payload = {
            "one_line": "" if one_line_tainted else orig_one_line,
            "topics": topic_entries,
            "quotes": quote_entries,
        }
        try:
            verdict = _call_review(request_fn, config, review_payload, diagnostics_dir)
        except Exception as exc:
            if not _is_model_declined_error(exc):
                raise
            verdict = _bisect_declined_review(
                request_fn,
                config,
                diagnostics_dir,
                one_line="" if one_line_tainted else orig_one_line,
                topic_entries=topic_entries,
                quote_entries=quote_entries,
            )

    drop_topic_set = {idx for idx in verdict["drop_topic_indices"] if type(idx) is int}
    strip_id_set = stripped_refs | {mid for mid in verdict["strip_message_ids"] if isinstance(mid, str) and mid}
    drop_quote_set = {idx for idx in verdict["drop_quote_indices"] if type(idx) is int}

    final_topics: list[dict[str, Any]] = []
    for pos, topic in enumerate(surviving_topics):
        if pos in drop_topic_set:
            continue
        kept_ids = [mid for mid in topic["message_ids"] if mid not in strip_id_set]
        if not kept_ids:
            continue
        final_topics.append({**topic, "message_ids": kept_ids})

    final_quotes: list[int] = []
    for q_idx in surviving_quotes:
        if q_idx in drop_quote_set:
            continue
        if type(q_idx) is int and 0 <= q_idx < len(candidates):
            if candidates[q_idx].get("message_id") in strip_id_set:
                continue
        final_quotes.append(q_idx)

    any_topic_dropped = len(final_topics) < len(orig_topics)
    candidate_replacement = verdict["replacement_one_line"].strip()
    replacement_safe = bool(
        candidate_replacement
        and check_hard_redlines(candidate_replacement, extra_terms) is None
        and not has_meta_filter_notice(candidate_replacement)
    )
    needs_one_line_rewrite = bool(
        any_topic_dropped
        or one_line_tainted
        or verdict["one_line_unsafe"]
        or has_meta_filter_notice(orig_one_line)
    )

    if not final_topics:
        if needs_one_line_rewrite or not orig_one_line.strip():
            final_one_line = candidate_replacement if replacement_safe else NEUTRAL_EMPTY_ONE_LINE
        else:
            final_one_line = orig_one_line
    elif needs_one_line_rewrite:
        if replacement_safe:
            final_one_line = candidate_replacement
        else:
            titles = [str(topic.get("title") or "").strip() for topic in final_topics[:3]]
            titles = [title for title in titles if title]
            final_one_line = "；".join(titles) + "。" if titles else NEUTRAL_EMPTY_ONE_LINE
    else:
        final_one_line = orig_one_line

    return {
        "one_line": final_one_line,
        "topics": final_topics,
        "quotes": final_quotes,
    }
