from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import json
import os
import subprocess
import sys
from .settings import OverlaySettings
from ..errors import CopilotError

REASONS={
    "accessibility_not_authorized":"未授予输入助手辅助功能权限。",
    "wrong_frontmost_app":"前台不是已核实的飞书应用。",
    "no_focused_composer":"没有识别到当前输入框。",
    "not_text_area":"不是受支持的多行输入框；不会写进搜索框。",
    "search_field":"当前焦点是搜索框，已阻止写入。",
    "not_enabled":"输入框不可编辑。",
    "nonempty_or_unreadable":"输入框已有草稿或无法读取；不会覆盖。",
    "not_settable":"本版飞书没有开放可写的 AXValue，请使用复制。",
    "focus_changed":"输入焦点已改变，已阻止写入。",
    "write_not_confirmed":"写入结果不确定，请直接检查飞书；不要连续重试。",
    "readback_mismatch":"已尝试写入，但未读回一致内容；请检查飞书。",
    "macos_only":"填入仅支持 macOS。",
    "invalid_request":"输入助手请求不合法。",
}
@dataclass(frozen=True)
class FillResult:
    status: str
    message: str
    verified: bool=False

class InputBridge:
    def __init__(self, base: Path, settings: OverlaySettings, *, runner=subprocess.run):
        self.base,self.settings,self.runner=base,settings,runner
    def helper(self):
        p=Path(self.settings.native_helper).expanduser()
        return p if p.is_absolute() else self.base/p
    def available(self):
        return sys.platform=="darwin" and self.settings.allow_fill and bool(self.settings.allowed_bundle_ids) and self.helper().is_file() and os.access(self.helper(),os.X_OK)
    def fill(self,text: str, *, confirmed: bool) -> FillResult:
        if not confirmed or not self.available():
            return FillResult("blocked","填入尚未通过本机配置和授权；可以使用复制。")
        if not isinstance(text,str) or not 1 <= len(text) <= 500 or "\x00" in text:
            return FillResult("blocked","候选文本无效。")
        env={k:v for k,v in os.environ.items() if not any(t in k.upper() for t in ("KEY","TOKEN","SECRET","PASSWORD"))}
        payload={"action":"fill","confirmed":True,"text":text,
                 "allowed_bundle_ids":list(self.settings.allowed_bundle_ids)}
        try:
            cp=self.runner([str(self.helper())],input=json.dumps(payload,ensure_ascii=False),
                           text=True,encoding="utf-8",capture_output=True,timeout=12,shell=False,env=env)
            data=json.loads(cp.stdout)
        except (subprocess.TimeoutExpired,OSError,json.JSONDecodeError,TypeError):
            return FillResult("unknown","填入结果未确认，请检查飞书；不会自动重试。")
        if not isinstance(data,dict):
            return FillResult("unknown","输入助手响应无效，请检查飞书。")
        if cp.returncode==0 and data.get("status")=="filled" and data.get("verified") is True and data.get("sent") is False:
            return FillResult("filled","已填入（读回确认），尚未发送。",True)
        reason=REASONS.get(data.get("reason"),"输入助手未确认成功，请检查飞书。")
        return FillResult("blocked" if data.get("status")=="blocked" else "unknown",reason)
