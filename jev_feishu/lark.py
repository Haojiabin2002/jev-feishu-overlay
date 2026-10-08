from __future__ import annotations
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from typing import Any, Callable
from .config import Config, Source, valid_id
from .errors import ContractError, PermissionDenied, ConfigError, CopilotError
from .messages import Message, parse_messages
from .privacy import outbound_text

class LarkError(CopilotError):
    pass

def unwrap(payload: Any) -> dict:
    if not isinstance(payload, dict):
        raise ContractError("飞书 CLI 响应不是 JSON 对象。")
    # Official CLI envelope is {ok, identity, data}; raw API payload may be nested.
    if "ok" in payload:
        if payload["ok"] is not True:
            raise LarkError("飞书 CLI 拒绝请求，请在本机检查授权和相应接口权限。")
        payload = payload.get("data")
    if not isinstance(payload, dict):
        raise ContractError("飞书 CLI data 字段错误。")
    if "code" in payload:
        if payload["code"] != 0:
            code = payload["code"]
            safe = str(code) if isinstance(code, int) else "unknown"
            raise LarkError(f"飞书接口返回错误码 {safe}；不输出可能含凭据的原始响应。")
        payload = payload.get("data", {})
    if not isinstance(payload, dict):
        raise ContractError("飞书业务数据不是对象。")
    return payload

class LarkCLI:
    """Narrow adapter: selected reads + send only as bot to configured owner."""
    def __init__(self, config: Config, runner: Callable = subprocess.run):
        self.cfg = config
        self.runner = runner

    def executable(self) -> str:
        path = shutil.which(self.cfg.cli)
        if not path:
            raise ConfigError("未找到 lark-cli；请让 Codex 复用已有 CLI 的绝对路径。")
        return path

    def _run(self, args: list[str]) -> dict:
        env = os.environ.copy()
        for key in {self.cfg.jev_key_env, self.cfg.llm_key_env, "OPENROUTER_API_KEY", "TYPESAFE_API_KEY"}:
            env.pop(key, None)
        try:
            cp = self.runner([self.executable(), *args, "--format", "json"],
                             capture_output=True, text=True, encoding="utf-8",
                             timeout=self.cfg.cli_timeout, shell=False, env=env)
        except subprocess.TimeoutExpired:
            raise LarkError("飞书 CLI 超时。若这是发送操作，发送结果可能未知；不会盲目重复发送。") from None
        except OSError:
            raise LarkError("无法执行飞书 CLI。") from None
        if cp.returncode != 0:
            # Do not echo stderr/stdout: they may contain user content or credentials.
            raise LarkError("飞书 CLI 执行失败。请在本机核查身份、权限与 CLI 帮助，勿上传完整调试日志。")
        if len(cp.stdout) > 2_000_000:
            raise ContractError("飞书 CLI 返回数据超过本地安全上限。")
        try:
            payload = json.loads(cp.stdout)
        except (json.JSONDecodeError, TypeError):
            raise ContractError("飞书 CLI 输出不是纯 JSON；请检查版本或包装脚本。") from None
        return unwrap(payload)

    def verify_owner(self) -> None:
        data = self._run(["api", "GET", "/open-apis/authen/v1/user_info", "--as", "user"])
        if data.get("open_id") != self.cfg.owner_open_id:
            raise PermissionDenied("当前 CLI 登录用户与配置所有者不一致，已停止；不会代另一用户读取会话。")

    def _resolve_p2p(self, user_id: str) -> str:
        # Read-only lookup despite HTTP POST. Shape verified in larksuite/cli/helpers.go.
        data = self._run(["api", "POST", "/open-apis/im/v1/chat_p2p/batch_query", "--as", "user",
                          "--params", json.dumps({"chatter_id_type": "open_id"}),
                          "--data", json.dumps({"chatter_ids": [user_id]})])
        chats = data.get("p2p_chats")
        if not isinstance(chats, list) or len(chats) != 1 or not isinstance(chats[0], dict):
            raise ContractError("单聊解析没有返回唯一会话；不会猜选其他聊天。")
        chat_id = chats[0].get("chat_id")
        if not valid_id(chat_id, "chat"):
            raise ContractError("单聊解析返回无效 chat_id。")
        return chat_id

    def _page(self, *, identity: str, chat_id: str = "", user_id: str = "",
              size: int = 30, token: str = "", since_ms: int | None = None) -> tuple[list[Message], bool, str]:
        if identity not in ("user", "bot") or bool(chat_id) == bool(user_id):
            raise PermissionDenied("不允许的消息读取参数。")
        if user_id:
            if identity != "user" or not valid_id(user_id, "user"):
                raise PermissionDenied("只有已授权用户可解析其单聊。")
            chat_id = self._resolve_p2p(user_id)
        if not valid_id(chat_id, "chat"):
            raise PermissionDenied("无效会话标识。")
        # Use CLI's raw API path. The shortcut formats timestamps to local MINUTES,
        # which is unsuitable for an incremental cursor, and performs extra enrichment.
        params = {"container_id_type": "chat", "container_id": chat_id,
                  "sort_type": "ByCreateTimeDesc", "page_size": size}
        if token:
            params["page_token"] = token
        if since_ms is not None:
            params["start_time"] = str(since_ms // 1000)
        args = ["api", "GET", "/open-apis/im/v1/messages", "--as", identity,
                "--params", json.dumps(params)]
        return parse_messages(self._run(args))

    def read_source(self, alias: str) -> tuple[list[Message], bool]:
        source = self.cfg.source(alias)  # Allowlist checked before any CLI call.
        self.verify_owner()  # Detect identity changes between analyses.
        items, more, _ = self._page(identity="user", chat_id=source.chat_id, user_id=source.user_id,
                                     size=self.cfg.max_context_messages)
        return items, more

    def read_inbox(self, since_ms: int) -> list[Message]:
        if not self.cfg.inbox_chat_id:
            raise ConfigError("尚未绑定机器人私聊。")
        all_items: dict[str, Message] = {}
        token = ""
        for _ in range(10):
            items, more, next_token = self._page(identity="bot", chat_id=self.cfg.inbox_chat_id,
                                                size=50, token=token, since_ms=since_ms)
            for item in items:
                all_items[item.message_id] = item
            if not more:
                return sorted(all_items.values(), key=lambda m: (m.created_ms, m.message_id))
            if not next_token or next_token == token:
                raise ContractError("消息分页游标缺失或重复，已停止而非跳过未读指令。")
            token = next_token
        raise ContractError("机器人私聊积压超过 500 条，请先人工检查；未静默截断。")

    def send_owner(self, text: str, request_key: str) -> dict:
        if not self.cfg.allow_bot_send:
            raise PermissionDenied("尚未同意由机器人向所有者私聊发送建议；allow_bot_send=false。")
        self.cfg.validate(live=True)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_key):
            raise ContractError("发送幂等键无效。")
        body = outbound_text(text)
        if len(body) > 7000:
            body = body[:6900] + "\n[报告过长，已截断；不会自动另发更多消息。]"
        # There is intentionally no send-to-chat or user-identity-send method.
        return self._run(["im", "+messages-send", "--as", "bot",
                          "--user-id", self.cfg.owner_open_id,
                          "--text=" + body, "--idempotency-key", request_key])
