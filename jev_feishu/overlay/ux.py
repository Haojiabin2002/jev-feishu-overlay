"""Human-facing copy, without altering Jev values or execution status.

This layer labels existing evidence. It must not synthesize hidden motives,
missing facts, successful connections or business actions.
"""
from __future__ import annotations
import os
import re
from ..models import AnalysisResult
from ..config import Config
from .viewmodel import Reply

TYPE_LABELS = {"Choice": "选择型", "Score": "评分型", "Noul": "是非型"}


def speaker_name(value: str) -> str:
    if value == "me": return "我"
    match = re.fullmatch(r"(?:person|participant)_(\d+)", str(value))
    return "发言者 " + match.group(1) if match else str(value)


def focus_text(result: AnalysisResult) -> str:
    chat = result.state.get("chat", {})
    message = next((m for m in chat.get("messages", [])
                    if m.get("id") == chat.get("focus_message_id")), None)
    if message is None:
        return "没有识别出具体要回复的消息，请先查看对话。"
    text = message.get("text", "")
    return speaker_name(message.get("speaker", "发言者"))+"："+text[:140]+("…" if len(text)>140 else "")


def readable_context(result: AnalysisResult, alias: str) -> str:
    chat = result.state.get("chat", {})
    messages = chat.get("messages", [])
    lines = [f"来源：{alias}", f"本次使用 {len(messages)} 条消息；不代表完整历史。", ""]
    for message in messages:
        speaker = speaker_name(message.get("speaker", "发言者未知"))
        marker = " ← 本次主要回复" if message.get("id") == chat.get("focus_message_id") else ""
        lines += [f"{speaker}{marker}", message.get("text", ""), ""]
    limits = chat.get("limitations", [])
    if limits: lines += ["没有覆盖的内容", *limits]
    return "\n".join(lines)


def readiness(cfg: Config) -> list[str]:
    """Configuration presence only; never claims that an endpoint was connected."""
    missing = []
    if not cfg.allow_cloud:
        missing.append("还没允许将选中的对话提交给模型服务。")
    credentials = [("判断模型 Jev", cfg.jev_key_env)]
    if cfg.llm_backend == "http":
        credentials.append(("写回复的 LLM", cfg.llm_key_env))
    for label, name in credentials:
        value = os.environ.get(name, "")
        if not value or value.startswith("REPLACE_"):
            missing.append(f"{label}：还没配置访问凭据。")
    return missing


def friendly_error(message: str) -> str:
    """Do not echo unknown payloads; raw provider responses may contain secrets."""
    text = str(message)
    if "allow_cloud" in text or "尚未同意" in text:
        return "还没允许将对话提交给模型。请在“连接 / 设置”查看需要确认的内容。"
    if "缺少环境变量" in text or "密钥" in text:
        return "模型凭据还没配置好。打开“连接 / 设置”，把检查任务交给本机 Codex；不要在聊天里发送密钥。"
    if "401" in text or "403" in text or "授权" in text or "权限" in text:
        return "连接或读取权限不足。请检查当前账号的授权，不要扩大到其他聊天。"
    if "超时" in text or "timeout" in text.lower() or "网络" in text:
        return "连接超时。这轮没有拿到完整结果；检查网络后可重试。"
    if "上限" in text or "限流" in text or "429" in text or "频繁" in text:
        return "请求已达到限制。先停止自动更新，稍后再试。"
    if "三条" in text or "结构" in text or "契约" in text or "JSON" in text:
        return "模型返回的内容没能正确读取。请重试；仍失败时让 Codex 检查接口格式。"
    if "404" in text:
        return "配置的模型或接口不可用，请在本机核对模型名称和访问权限。"
    if "找不到" in text or "不存在" in text:
        return "缺少运行所需的配置或程序。打开“连接 / 设置”检查，不会因此读取其他聊天。"
    if "取消" in text:
        return "本轮已停止；已发出的请求可能仍会计费。"
    return "这轮没有完成。请重试；仍失败时打开“连接 / 设置”检查。"


def friction_label(answer: dict) -> tuple[str, str]:
    """Display the most likely existing rubric bin, not rounded expected value."""
    probs = answer.get("probabilities", {})
    if not probs: return "暂无法判断", "amber"
    key = max(probs, key=probs.get)
    if float(probs[key]) < .5: return "判断有分歧，先看具体消息", "amber"
    return {"0": ("正常沟通", "green"), "1": ("有提醒或纠正", "green"),
            "2": ("有明确分歧", "amber"), "3": ("有指责或升级提醒", "red"),
            "4": ("有明确冲突", "red")}.get(key, ("暂无法判断", "amber"))


def next_step_text(result: AnalysisResult) -> str:
    key = result.judgement.get("next_action", {}).get("choice")
    return {"clarify": "先问清对方需要什么，再决定能否承诺。",
            "check_facts": "先查清缺少的信息，别把猜测写成事实。",
            "answer": "直接回答已确认的内容，不额外承诺。",
            "propose": "给一个可讨论的安排，请对方确认。",
            "acknowledge": "先回应对方已经明确提出的问题。",
            "no_reply": "可以先不回复，避免重复确认。"}.get(key, "先确认这句话是否需要你回复。")


def draft_badge(reply: Reply) -> tuple[str, bool]:
    if reply.grounded is None:
        return "还没核查：先阅读，不要当作已确认的事实。", True
    if not reply.usable and "阈值" in reply.caution:
        return "可能包含未确认的信息，先修改或核实。", True
    if "均不采用" in reply.caution:
        return "本轮不建议直接使用；可按实际情况重新写。", True
    if not reply.usable:
        return "暂不建议直接使用，请先检查内容。", True
    return "仍需你确认内容；不会自动发送。", False


def fill_explanation(reply: Reply, *, demo: bool, busy: bool, stale: bool,
                     manual: bool, available: bool) -> str:
    if demo: return "示例不能填入飞书，可以试用复制。"
    if stale: return "旧建议已停用，请重新分析。"
    if busy: return "本轮检查结束后，才可复制或填入。"
    if not reply.usable: return "这条暂不能填入，可以先改写并核对。"
    if manual: return "粘贴模式不确定目标聊天，请复制后自行粘贴。"
    if not available: return "本机尚未启用填入，请先复制后粘贴。"
    return ""
