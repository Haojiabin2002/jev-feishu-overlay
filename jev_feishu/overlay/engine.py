from __future__ import annotations
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable
import hashlib
import json
import threading
import time
import sqlite3
from ..config import Config
from ..demo import DemoClient, TRANSCRIPT
from ..errors import CopilotError
from ..lark import LarkCLI
from ..messages import build_state, pasted_messages
from ..models import AnalysisResult, Analyzer
from ..store import Store
from .history import HistoryStore,anchor_hash

@dataclass
class Outcome:
    signature: str
    alias: str
    result: AnalysisResult | None = None
    notice: str = ""
    invalidates: bool = False
    read_at: float = 0
    preview_only: bool = False
    history_id: str = ''
    history_error: str = ''

class OverlayEngine:
    """Reads only the explicit source. Never instantiates a bot inbox or sends reports.

    A read-only selected source, not automatic discovery of the foreground chat.
    Auto mode compares the complete bounded snapshot (including edits), so own
    replies invalidate old suggestions instead of silently retaining a stale draft.
    """
    def __init__(self, cfg: Config, *, lark=None, analyzer=None):
        self.cfg=cfg
        self.lark=lark or LarkCLI(cfg)
        self.analyzer=analyzer

    def capture(self, *, alias: str, cancelled=None, on_event=None) -> Outcome:
        """Read the selected source locally without uploading to a model or consuming quota."""
        stop=cancelled or threading.Event();emit=on_event or (lambda *args:None)
        if stop.is_set():raise CopilotError('已取消读取。')
        source=self.cfg.source(alias);self.cfg.validate(live=True)
        emit('capture','running',None)
        messages,more=self.lark.read_source(alias)
        if stop.is_set():raise CopilotError('已取消，迟到的读取结果不再采用。')
        signature=hashlib.sha256(json.dumps({'alias':alias,'messages':[asdict(m) for m in messages]},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        state=build_state(messages,self.cfg,source,older_available=more)
        emit('capture','done',None)
        return Outcome(signature,alias,AnalysisResult(state),notice=f'已读取 {len(messages)} 条飞书消息；尚未调用模型。',
                       read_at=time.time(),preview_only=True)

    def run(self, *, alias: str="", text: str="", automatic: bool=False,
            previous: str="", cancelled: threading.Event | None=None,
            on_event: Callable | None=None) -> Outcome:
        stop=cancelled or threading.Event()
        emit=on_event or (lambda *args: None)
        if stop.is_set():
            raise CopilotError("已取消。")
        model_cfg=self.cfg.model_config_for(alias) if alias else self.cfg
        model_cfg.check_models()  # Consent gate before any source reads.
        source=self.cfg.source(alias) if alias else None
        emit("capture","running",None)
        if source:
            self.cfg.validate(live=True)
            messages, more=self.lark.read_source(alias)
        else:
            if automatic:
                raise CopilotError("粘贴文本模式不允许自动刷新。")
            messages=pasted_messages(text,self.cfg.owner_open_id or "me_local",self.cfg.max_input_chars)
            more=False
        if stop.is_set():
            raise CopilotError("读取已结束，分析已取消。")
        digest=hashlib.sha256(json.dumps({"alias":alias,"messages":[asdict(m) for m in messages]},
                                        ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        emit("capture","done",None)
        out=Outcome(digest,alias or "手动文本",read_at=time.time())
        if automatic and digest==previous:
            out.notice="没有新消息；未调用模型。"
            return out
        if automatic and messages and (messages[-1].sender_id==self.cfg.owner_open_id or messages[-1].sender_type in ("app","bot")):
            out.notice="最新发言来自你或机器人；旧建议已失效，本轮不调用模型。"
            out.invalidates=True
            return out
        # Local text mode can run without any Feishu identity or bot permissions.
        from dataclasses import replace
        state_cfg=self.cfg if self.cfg.owner_open_id else replace(self.cfg,owner_open_id="me_local")
        state=build_state(messages,state_cfg,source,older_available=more)
        if stop.is_set():
            raise CopilotError("已取消；没有发送模型请求。")
        store=Store(self.cfg)  # SQLite connection is opened/closed in this worker only.
        try:
            store.consume_analysis()
            store.prune()
        finally:
            store.close()
        out.result=(self.analyzer or Analyzer(model_cfg)).analyze(state,on_update=emit,cancelled=stop.is_set)
        if source and not out.result.demo:
            # build_state retains a suffix and gives its entries sequential m1... IDs.
            entries=state['chat']['messages']
            index=next((i for i,m in enumerate(entries) if m['id']==state['chat']['focus_message_id']),None)
            anchor=messages[-len(entries):][index] if index is not None else None
            anchor_text=anchor.text if anchor else ''
            if anchor and sum(anchor_hash(m.text)==anchor_hash(anchor_text) for m in messages)!=1:
                anchor_text=''  # Repeated text cannot identify an original message reliably.
            try:
                with HistoryStore(self.cfg) as history:
                    out.history_id=history.save(alias,digest,out.result,
                        message_id=anchor.message_id if anchor else '',anchor_text=anchor_text)
            except (OSError,sqlite3.Error,ValueError,TypeError):
                out.history_error='本轮分析已完成，但本机记录保存失败；请检查项目目录的写入权限。'
        return out

def demo_result(base: Path) -> AnalysisResult:
    cfg=Config(base=base,owner_open_id="ou_demo_owner")
    state=build_state(pasted_messages(TRANSCRIPT,cfg.owner_open_id),cfg)
    # Fixed demo data needs no worker pool; keep it on the UI/main thread.
    client=DemoClient()
    judgement,_=client.judge(state)
    candidates,_=client.generate(state)
    ranking,_=client.rank(state,candidates)
    return AnalysisResult(state,judgement=judgement,candidates=candidates,ranking=ranking,
                          demo=True,timings_ms={"judgement":0,"candidates":0,"ranking":0,"total":0})
