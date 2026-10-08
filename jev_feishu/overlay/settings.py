from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import re
import tomllib
from ..config import check_private
from ..errors import ConfigError

@dataclass(frozen=True)
class OverlaySettings:
    opacity: float = 0.97
    width: int = 490
    height: int = 820
    refresh_seconds: int = 15
    allow_fill: bool = False
    allowed_bundle_ids: tuple[str, ...] = ()
    native_helper: str = "native/jev-input-helper"
    fill_delay_seconds: int = 4
    follow_window: bool = False
    follow_bundle_id: str = "com.electron.lark"
    window_helper: str = "native/jev-window-follow"
    message_annotations: bool = False
    message_anchor_helper: str = "native/jev-message-anchors"

    def validate(self):
        if isinstance(self.opacity, bool) or not isinstance(self.opacity, (int, float)) or not .65 <= self.opacity <= 1:
            raise ConfigError("悬浮窗 opacity 必须在 0.65–1 之间。")
        for name, lo, hi in (("width",440,720),("height",580,1200),("refresh_seconds",10,300),("fill_delay_seconds",3,10)):
            v=getattr(self,name)
            if type(v) is not int or not lo <= v <= hi:
                raise ConfigError(f"悬浮窗 {name} 超出允许范围。")
        if type(self.allow_fill) is not bool:
            raise ConfigError("allow_fill 必须为布尔值。")
        if type(self.follow_window) is not bool:
            raise ConfigError("follow_window 必须为布尔值。")
        if type(self.message_annotations) is not bool:
            raise ConfigError("message_annotations 必须为布尔值。")
        if not isinstance(self.message_anchor_helper,str) or not self.message_anchor_helper:
            raise ConfigError("message_anchor_helper 路径无效。")
        if not isinstance(self.follow_bundle_id,str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{2,150}",self.follow_bundle_id):
            raise ConfigError("跟随目标必须是本机核实的明确 Bundle ID。")
        if not isinstance(self.window_helper,str) or not self.window_helper:
            raise ConfigError("window_helper 路径无效。")
        if not isinstance(self.allowed_bundle_ids, (tuple,list)) or any(not isinstance(v,str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{2,150}",v) for v in self.allowed_bundle_ids):
            raise ConfigError("allowed_bundle_ids 必须是本机核实过的飞书 Bundle ID 数组。")
        if self.allow_fill and not self.allowed_bundle_ids:
            raise ConfigError("启用填入前需要核实飞书 Bundle ID。")
        if not isinstance(self.native_helper,str) or not self.native_helper:
            raise ConfigError("native_helper 路径无效。")

def load_settings(path: Path) -> OverlaySettings:
    if not path.exists():
        return OverlaySettings()
    check_private(path)
    try:
        data=tomllib.loads(path.read_text(encoding="utf-8"))
        if set(data)-{"overlay"}:
            raise ConfigError("overlay.toml 含未知顶层字段。")
        result=OverlaySettings(**data.get("overlay",{}))
        result.validate()
        return result
    except (TypeError, ValueError, OSError, tomllib.TOMLDecodeError):
        raise ConfigError("overlay.toml 格式错误。") from None
