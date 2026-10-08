from __future__ import annotations
from dataclasses import replace
import json
import os
from .config import Config, check_private, valid_id
from .errors import ConfigError, ContractError, PermissionDenied

def save_binding(cfg: Config, response: dict) -> str:
    chat_id = response.get("chat_id")
    if not chat_id and isinstance(response.get("message"), dict):
        chat_id = response["message"].get("chat_id")
    if not valid_id(chat_id, "chat"):
        raise ContractError("测试消息可能已发送，但返回值缺少 chat_id；请核对本机 CLI 契约，不能猜测私聊 ID。")
    cfg.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(cfg.state_dir, 0o700)
    path = cfg.state_dir / "binding.json"
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing.get("owner_open_id") != cfg.owner_open_id:
            raise PermissionDenied("已有不同所有者的绑定。请在本机明确确认切换后移除旧绑定，不自动覆盖。")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"owner_open_id": cfg.owner_open_id, "inbox_chat_id": chat_id,
                   "provenance": "chat_id returned by bot send-to-owner"}, f, ensure_ascii=False, indent=2)
    os.chmod(path, 0o600)
    return chat_id

def resolve_binding(cfg: Config) -> Config:
    path = cfg.state_dir / "binding.json"
    if not path.exists():
        raise ConfigError("未建立私人机器人绑定。请先经确认执行 bind --send。")
    check_private(path)
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ConfigError("绑定文件损坏，请在本机重新绑定。") from None
    if not isinstance(obj, dict) or obj.get("owner_open_id") != cfg.owner_open_id:
        raise PermissionDenied("绑定所有者不匹配。")
    chat_id = obj.get("inbox_chat_id")
    if not valid_id(chat_id, "chat"):
        raise ConfigError("绑定私聊 ID 无效。")
    if cfg.inbox_chat_id and cfg.inbox_chat_id != chat_id:
        raise PermissionDenied("手填私聊与服务端返回的绑定不一致，已停止。")
    return replace(cfg, inbox_chat_id=chat_id)
