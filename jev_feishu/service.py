from __future__ import annotations
from dataclasses import dataclass
import time
from .config import Config
from .errors import CopilotError, ContractError, PermissionDenied
from .lark import LarkCLI
from .messages import Message, build_state, pasted_messages
from .models import Analyzer
from .render import render
from .store import Store

@dataclass(frozen=True)
class Command:
    kind: str
    argument: str = ""

def parse_command(text: str) -> Command:
    text = text.strip()
    if text.lower().startswith("jev "):
        text = text[4:].strip()
    if text in ("帮助", "help", "/help"):
        return Command("help")
    if text in ("会话", "sessions", "/sessions"):
        return Command("sessions")
    if text in ("状态", "status", "/status"):
        return Command("status")
    if text in ("暂停", "pause", "/pause"):
        return Command("pause")
    if text in ("恢复", "resume", "/resume"):
        return Command("resume")
    parts = text.split(maxsplit=1)
    if len(parts) == 2 and parts[0] in ("分析", "/analyze"):
        alias = parts[1].strip()
        if any(c.isspace() for c in alias) or len(alias) > 32:
            raise ContractError("用法：分析 会话别名。别名以本地白名单为准，不接受任意会话 ID。")
        return Command("analyze", alias)
    if len(parts) == 2 and parts[0] in ("文本", "/text"):
        return Command("text", parts[1])
    return Command("help")

class Service:
    def __init__(self, cfg: Config, lark: LarkCLI, analyzer: Analyzer, store: Store):
        self.cfg, self.lark, self.analyzer, self.store = cfg, lark, analyzer, store

    def help(self) -> str:
        aliases = "、".join(s.alias for s in self.cfg.sources if s.enabled) or "尚未启用；可先用“文本”模式"
        return ("Jev 私人工作沟通助手\n"
                "分析 会话别名 —— 读取本地白名单中该会话的最近消息\n"
                "文本 我：……\\n同事：…… —— 分析你手动粘贴的对话（支持真实换行）\n"
                "会话 / 状态 / 暂停 / 恢复 / 帮助\n"
                f"已启用别名：{aliases}\n"
                "只在这个机器人私聊提供建议，不会代你给同事发消息。内容会提交到你配置的模型服务。")

    def dispatch(self, command: Command) -> str:
        if command.kind == "help":
            return self.help()
        if command.kind == "sessions":
            aliases = [s.alias for s in self.cfg.sources if s.enabled]
            return "已授权会话：" + ("、".join(aliases) or "无；请在本机配置，或用文本模式。")
        if command.kind == "status":
            return (f"服务在线｜分析{'暂停' if self.store.get('paused') == '1' else '可用'}\n"
                    f"指令轮询：{self.cfg.poll_seconds:g} 秒｜最多 {self.cfg.max_context_messages} 条上下文\n"
                    f"每日分析上限：{self.cfg.daily_analysis_limit}（UTC 日界）；每分钟 {self.cfg.per_minute_limit} 次\n"
                    "不自动监控所有会话，不自动发送业务回复。")
        if command.kind in ("pause", "resume"):
            self.store.set("paused", "1" if command.kind == "pause" else "0")
            return "已暂停模型分析；仍响应帮助与恢复。" if command.kind == "pause" else "已恢复分析。"
        if self.store.get("paused") == "1":
            return "分析已暂停。发送“恢复”后再试。"
        # Permission and cloud consent are checked before reading source content.
        self.cfg.check_models()
        source = self.cfg.source(command.argument) if command.kind == "analyze" else None
        if command.kind == "text" and len(command.argument) > self.cfg.max_input_chars:
            raise ContractError("粘贴文本过长，未调用模型。")
        self.store.consume_analysis()
        if source:
            messages, more = self.lark.read_source(source.alias)
        else:
            messages = pasted_messages(command.argument, self.cfg.owner_open_id, self.cfg.max_input_chars)
            more = False
        state = build_state(messages, self.cfg, source, older_available=more)
        result = self.analyzer.analyze(state)
        return render(result, self.cfg, source.alias if source else "手动粘贴")

    def once(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        now_ms = int(now*1000)
        # Fresh service starts at now, rather than replaying old bot conversations.
        previous = self.store.get("cursor_ms")
        if not previous:
            self.store.set("cursor_ms", str(now_ms))
            self.store.set("initial_ms", str(now_ms))
            return {"initialized": True, "processed": 0, "ignored": 0}
        cursor = int(previous)
        since = max(cursor-2000, now_ms-self.cfg.max_backlog_seconds*1000,
                    int(self.store.get("initial_ms", "0")))
        messages = self.lark.read_inbox(since)
        processed = ignored = 0
        for message in messages:
            if message.created_ms < since:
                continue
            if message.created_ms > now_ms + 60_000:
                raise ContractError("消息时间异常，未推进游标；请检查本机时钟。")
            cursor = max(cursor, message.created_ms)
            # Owner binding + explicit platform user sender: model/chat cannot change identity.
            if message.sender_id != self.cfg.owner_open_id or message.sender_type != "user" or message.kind != "text":
                ignored += 1
                continue
            if not self.store.reserve(message.message_id):
                continue
            request_key = "jev_" + self.store.key(message.message_id)
            try:
                command = parse_command(message.text)
                report = self.dispatch(command)
            except CopilotError as exc:
                report = "Jev 助手未完成本次分析：" + str(exc)
            except Exception:
                report = "Jev 助手内部错误，本次已停止；未输出原始数据，请检查本机测试与契约。"
            try:
                self.lark.send_owner(report, request_key)
                self.store.done(message.message_id)
            except CopilotError:
                # Unknown delivery must not trigger model reruns or uncontrolled resends.
                self.store.done(message.message_id, "failed")
                raise CopilotError("向所有者私聊发送失败或结果未知。请在飞书确认；可重新发送指令，不自动重放。") from None
            processed += 1
        self.store.set("cursor_ms", str(cursor))
        self.store.prune()
        return {"initialized": False, "processed": processed, "ignored": ignored}

    def run(self, stop=None) -> None:
        failures = 0
        while stop is None or not stop.is_set():
            try:
                summary = self.once()
                failures = 0
                if summary["initialized"] or summary["processed"]:
                    print(f"event=poll initialized={summary['initialized']} processed={summary['processed']}", flush=True)
            except CopilotError as exc:
                failures += 1
                # Only error class, never text, IDs, response bodies, or credentials in service logs.
                print(f"event=poll_error type={type(exc).__name__} consecutive={failures}", flush=True)
            delay = min(60.0, self.cfg.poll_seconds * (2 ** min(failures, 4)))
            if stop is None:
                time.sleep(delay)
            else:
                stop.wait(delay)
