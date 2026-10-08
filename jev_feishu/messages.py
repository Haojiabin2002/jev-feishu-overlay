from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
from typing import Any
from .config import Config, Source
from .errors import ContractError
from .privacy import clean_text

@dataclass(frozen=True)
class Message:
    message_id: str
    sender_id: str
    sender_type: str
    text: str
    created_ms: int
    kind: str = "text"
    thread_id: str = ""
    missing_content: bool = False
    mentions: tuple[tuple[str, str], ...] = ()

def timestamp_ms(value: Any) -> int:
    try:
        if isinstance(value, bool):
            raise ValueError()
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
            n = float(value)
            return int(n if n >= 1e11 else n * 1000)
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if d.tzinfo is None:
            raise ValueError()
        return int(d.timestamp() * 1000)
    except (ValueError, OverflowError, TypeError):
        raise ContractError("消息时间字段不符合已核对的接口契约。") from None

def _post_text(obj: Any) -> str:
    # Do not fetch links or attachments. Retain textual content only.
    if isinstance(obj, str):
        return obj
    if isinstance(obj, list):
        return "\n".join(filter(None, (_post_text(i) for i in obj)))
    if isinstance(obj, dict):
        if "text" in obj and isinstance(obj["text"], str):
            return obj["text"]
        if obj.get("tag") in ("img", "media"):
            return "[媒体未解析]"
        if obj.get("tag") == "at":
            return "[提及成员]"
        if "content" in obj:
            return "\n".join(filter(None, [str(obj.get("title", "")), _post_text(obj["content"])]))
        if "zh_cn" in obj:
            return _post_text(obj["zh_cn"])
        if "en_us" in obj:
            return _post_text(obj["en_us"])
    return ""

def parse_messages(data: dict) -> tuple[list[Message], bool, str]:
    raw = data.get("messages", data.get("items"))
    if not isinstance(raw, list):
        raise ContractError("飞书 CLI 未返回 messages/items 数组；请核对本机版本，不能把契约不符视为空聊天。")
    if not isinstance(data.get("has_more", False), bool):
        raise ContractError("飞书分页字段类型错误。")
    items: list[Message] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ContractError("消息元素格式错误。")
        if item.get("deleted") or item.get("recalled"):
            continue
        sender = item.get("sender") or {}
        if not isinstance(sender, dict):
            raise ContractError("消息 sender 格式错误。")
        sid = sender.get("id") or sender.get("open_id") or ""
        if isinstance(sid, dict):
            sid = sid.get("open_id", "")
        stype = sender.get("sender_type") or sender.get("type") or ""
        kind = str(item.get("msg_type", item.get("message_type", "")))
        mid = item.get("message_id", "")
        if not isinstance(mid, str) or not mid:
            raise ContractError("消息缺少 message_id。")
        content = item.get("content", (item.get("body") or {}).get("content", ""))
        if isinstance(content, str) and content.lstrip().startswith(("{", "[")):
            try:
                parsed = json.loads(content)
                content = parsed if isinstance(parsed, (dict, list)) else content
            except json.JSONDecodeError:
                pass
        missing = kind not in ("text", "post")
        if missing:
            text = f"[{kind or '未知类型'} 消息未解析]"
        else:
            text = _post_text(content)
            if not text:
                text, missing = "[正文缺失]", True
        if "[媒体未解析]" in text:
            missing = True
        mention_ids = []
        for mention in item.get("mentions", []) or []:
            if not isinstance(mention, dict):
                continue
            who = mention.get("id", "")
            if isinstance(who, dict):
                who = who.get("open_id", "")
            if isinstance(who, str) and who:
                mention_ids.append((who, str(mention.get("key", ""))))
        items.append(Message(mid, str(sid), str(stype), text,
                             timestamp_ms(item.get("create_time")), kind,
                             str(item.get("thread_id", "")), missing, tuple(mention_ids)))
    # Do not collapse multiple people into 'other'.
    items.sort(key=lambda x: (x.created_ms, x.message_id))
    return items, data.get("has_more", False), str(data.get("page_token", ""))

def pasted_messages(text: str, owner: str, limit: int = 12000) -> list[Message]:
    if not text.strip() or len(text) > limit:
        raise ContractError(f"粘贴文本须非空且不超过 {limit} 字。")
    result: list[Message] = []
    speaker = "pasted_unknown"
    buffers: list[str] = []
    def flush() -> None:
        if buffers:
            result.append(Message(f"pasted_{len(result) + 1}", speaker, "user", "\n".join(buffers), len(result)+1))
            buffers.clear()
    for line in text.splitlines():
        m = re.match(r"^([^:：\n]{1,24})[:：]\s*(.*)$", line)
        if m:
            flush()
            name = m.group(1).strip()
            speaker = owner if name in ("我", "本人", "me") else "pasted:" + name
            buffers.append(m.group(2))
        else:
            buffers.append(line)
    flush()
    return result

def build_state(messages: list[Message], cfg: Config, source: Source | None = None,
                *, older_available: bool = False) -> dict:
    if not messages:
        raise ContractError("没有可分析的消息。")
    chosen = messages[-cfg.max_context_messages:]
    mapping: dict[str, str] = {}
    entries: list[dict] = []
    remaining = cfg.max_context_chars
    warnings: list[str] = []
    for item in reversed(chosen):
        text = clean_text(item.text, redact=cfg.redact)
        if len(text) > cfg.max_message_chars:
            text = text[:cfg.max_message_chars] + "…[单条截断]"
            warnings.append("有单条消息被截断")
        if len(text) > remaining:
            warnings.append("上下文字数达到上限，已省略较早消息")
            break
        remaining -= len(text)
        entries.append({"_sender": item.sender_id or f"unknown_{item.message_id}",
                        "_is_bot": item.sender_type in ("app", "bot"),
                        "text": text, "kind": item.kind,
                        "thread": item.thread_id, "_created": item.created_ms, "_mentions": item.mentions})
        if item.missing_content:
            warnings.append("含图片、卡片或其他未解析内容")
        if item.thread_id:
            warnings.append("存在话题回复；本次不保证包含完整话题链")
    entries.reverse()
    threads: dict[str, str] = {}
    for index, entry in enumerate(entries):
        sid = entry.pop("_sender")
        is_bot = entry.pop("_is_bot")
        if sid == cfg.owner_open_id:
            role = "me"
        else:
            if sid not in mapping:
                mapping[sid] = f"participant_{len(mapping)+1}"
            role = mapping[sid]
        normalized_mentions = []
        for uid, mention_key in entry.pop("_mentions"):
            if uid == cfg.owner_open_id:
                mention_role = "me"
            else:
                if uid not in mapping:
                    mapping[uid] = f"participant_{len(mapping)+1}"
                mention_role = mapping[uid]
            normalized_mentions.append(mention_role)
            if mention_key:
                entry["text"] = entry["text"].replace(mention_key, "@" + mention_role)
        entry["mentions"] = list(dict.fromkeys(normalized_mentions))
        entry["speaker"] = role
        entry["speaker_is_bot"] = is_bot
        entry["id"] = f"m{index+1}"
        raw_thread = entry["thread"]
        if raw_thread:
            threads.setdefault(raw_thread, f"thread_{len(threads)+1}")
            entry["thread"] = threads[raw_thread]
        entry.pop("_created")
    if not entries:
        raise ContractError("截取后没有可分析内容。")
    if older_available or len(messages) > len(entries):
        warnings.append("仅使用截取的最近消息，不是完整历史")
    # Latest incoming human message is the focus, but retain any subsequent own reply.
    incoming = [e for e in entries if e["speaker"] != "me" and not e["speaker_is_bot"]]
    focus = incoming[-1]["id"] if incoming else entries[-1]["id"]
    if not incoming:
        warnings.append("没有识别到他人发言，不宜推断对方诉求")
    return {"chat": {
        "relationship": clean_text(source.relationship if source else "用户手动提供的对话", redact=cfg.redact),
        "background": clean_text(source.context if source else "发言标签由用户提供，未经平台核实。", redact=cfg.redact),
        "participants": ["me", *mapping.values()],
        "messages": entries,
        "focus_message_id": focus,
        "latest_from": entries[-1]["speaker"],
        "limitations": sorted(set(warnings)),
    }}
