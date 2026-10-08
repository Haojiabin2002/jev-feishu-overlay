from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from urllib.parse import urlsplit
import os
import re
import stat
import tomllib
from .errors import ConfigError, PermissionDenied

ID_PATTERNS = {"user": re.compile(r"ou[_-][A-Za-z0-9_-]{3,120}\Z"),
               "chat": re.compile(r"oc[_-][A-Za-z0-9_-]{3,120}\Z")}

def valid_id(value: str, kind: str) -> bool:
    return isinstance(value, str) and bool(ID_PATTERNS[kind].fullmatch(value))

def check_private(path: Path) -> None:
    if path.exists() and os.name == "posix" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ConfigError(f"{path.name} 权限过宽，请执行 chmod 600 后重试。")

def load_env_file(path: Path) -> None:
    """Data-only dotenv parser: no shell expansion and no command execution."""
    if not path.exists():
        return
    check_private(path)
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ConfigError(".env 格式错误；仅允许 KEY=value。")
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ConfigError(".env 含无效变量名。")
        if value[:1] in ("'", '"'):
            if len(value) < 2 or value[-1] != value[0]:
                raise ConfigError(".env 引号不匹配。")
            value = value[1:-1]
        # Existing explicit environment values take precedence.
        os.environ.setdefault(key, value)

@dataclass(frozen=True)
class Source:
    alias: str
    chat_id: str = ""
    user_id: str = ""
    relationship: str = "工作协作关系"
    context: str = ""
    enabled: bool = False
    allow_cloud: bool = False

    display_name: str = ""

    def validate(self) -> None:
        if not self.alias or len(self.alias) > 32 or re.search(r"\s", self.alias):
            raise ConfigError("会话别名必须为 1–32 个字符，且不能包含空白。")
        if bool(self.chat_id) == bool(self.user_id):
            raise ConfigError("每个会话必须且只能设置 chat_id 或 user_id。")
        if self.chat_id and not valid_id(self.chat_id, "chat"):
            raise ConfigError("会话 chat_id 无效。")
        if self.user_id and not valid_id(self.user_id, "user"):
            raise ConfigError("会话 user_id 必须是 open_id。")
        if len(self.context) > 1500 or len(self.relationship) > 150:
            raise ConfigError("会话背景过长。")
        if not isinstance(self.display_name,str) or len(self.display_name)>128 or any(c in self.display_name for c in '\r\n\t'):
            raise ConfigError("会话显示名称无效。")

@dataclass(frozen=True)
class Config:
    base: Path
    cli: str = "lark-cli"
    owner_open_id: str = ""
    inbox_chat_id: str = ""
    allow_cloud: bool = False
    allow_bot_send: bool = False
    poll_seconds: float = 5.0
    max_context_messages: int = 30
    max_context_chars: int = 14000
    max_message_chars: int = 1600
    max_input_chars: int = 12000
    daily_analysis_limit: int = 100
    per_minute_limit: int = 6
    max_backlog_seconds: int = 600
    cli_timeout: float = 30.0
    model_timeout: float = 25.0
    redact: bool = True
    jev_url: str = "https://openrouter.ai/api/alpha/decisions"
    jev_model: str = "typesafe/jev-1.13"
    jev_key_env: str = "JEV_API_KEY"
    llm_url: str = "https://openrouter.ai/api/v1/chat/completions"
    llm_model: str = "deepseek/deepseek-chat-v3.1"
    llm_key_env: str = "LLM_API_KEY"
    llm_backend: str = "http"
    codex_binary: str = "codex"
    node_binary: str = "node"
    codex_model: str = ""
    codex_reasoning_effort: str = "high"
    json_mode: bool = True
    minimum_grounding: float = 0.70
    sources: tuple[Source, ...] = field(default_factory=tuple)

    @property
    def state_dir(self) -> Path:
        return self.base / ".state"

    def source(self, alias: str) -> Source:
        for source in self.sources:
            if source.alias == alias and source.enabled:
                return source
        from .errors import PermissionDenied
        raise PermissionDenied("未授权的会话别名。请在本地配置中确认并启用；聊天指令不能扩大权限。")

    def model_config_for(self, alias: str) -> Config:
        """Bind persistent model consent to exactly one explicitly selected source."""
        source=self.source(alias)
        return replace(self,allow_cloud=self.allow_cloud or source.allow_cloud,sources=(source,))

    def validate(self, *, live: bool = False, require_inbox: bool = False) -> None:
        if self.llm_backend not in ("http", "codex_sdk"):
            raise ConfigError("生成后端必须为 http 或 codex_sdk。")
        if self.codex_reasoning_effort not in ("minimal", "low", "medium", "high", "xhigh", "max", "ultra"):
            raise ConfigError("Codex 推理强度无效。")
        for key in ("allow_cloud", "allow_bot_send", "redact", "json_mode"):
            if type(getattr(self, key)) is not bool:
                raise ConfigError("同意开关必须为 TOML 布尔值，不能使用字符串。")
        if any(type(source.enabled) is not bool for source in self.sources):
            raise ConfigError("会话 enabled 必须是布尔值。")
        if any(type(source.allow_cloud) is not bool for source in self.sources):
            raise ConfigError("会话 allow_cloud 必须是布尔值。")
        if not 3 <= self.poll_seconds <= 300:
            raise ConfigError("轮询间隔必须在 3–300 秒之间。")
        for v, low, high, name in [
            (self.max_context_messages, 1, 50, "上下文消息数"),
            (self.max_context_chars, 500, 40000, "上下文字数"),
            (self.max_message_chars, 100, 4000, "单条消息字数"),
            (self.max_input_chars, 100, 20000, "粘贴输入字数"),
            (self.daily_analysis_limit, 1, 1000, "每日分析上限"),
            (self.per_minute_limit, 1, 30, "每分钟分析上限"),
            (self.max_backlog_seconds, 30, 3600, "离线积压窗口"),
        ]:
            if not isinstance(v, int) or isinstance(v, bool) or not low <= v <= high:
                raise ConfigError(f"{name}超出允许范围。")
        if not 1 <= self.cli_timeout <= 120 or not 1 <= self.model_timeout <= 120:
            raise ConfigError("请求超时必须在 1–120 秒之间。")
        if not 0 <= self.minimum_grounding <= 1:
            raise ConfigError("事实约束阈值必须在 0–1 之间。")
        if len({s.alias for s in self.sources}) != len(self.sources):
            raise ConfigError("会话别名重复。")
        for source in self.sources:
            if source.enabled:
                source.validate()
        for endpoint in (self.jev_url, self.llm_url):
            u = urlsplit(endpoint)
            if u.scheme != "https" or not u.hostname or u.username or u.password or u.fragment:
                raise ConfigError("模型地址必须为不含用户名、密码和片段的 HTTPS URL。")
        for key_name in (self.jev_key_env, self.llm_key_env):
            if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key_name):
                raise ConfigError("密钥环境变量名称无效。")
        if live:
            if not valid_id(self.owner_open_id, "user"):
                raise ConfigError("需要配置所有者 owner_open_id。")
            if require_inbox and not valid_id(self.inbox_chat_id, "chat"):
                raise ConfigError("需要先绑定机器人私聊 inbox_chat_id。")

    def check_models(self) -> None:
        if not self.allow_cloud:
            raise PermissionDenied("尚未同意把选定内容发送到配置的模型服务；allow_cloud=false。")
        names = (self.jev_key_env,) if self.llm_backend == "codex_sdk" else (self.jev_key_env, self.llm_key_env)
        for name in names:
            value = os.environ.get(name, "")
            if not value or value.startswith("REPLACE_"):
                raise ConfigError(f"缺少环境变量 {name}；请在本机安全配置，勿把密钥发进聊天。")

def load_config(path: str | Path) -> Config:
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise ConfigError("找不到配置文件。请先运行 scripts/bootstrap.sh。")
    check_private(p)
    try:
        data = tomllib.loads(p.read_text(encoding="utf-8"))
        allowed = {"feishu", "privacy", "runtime", "models", "sources"}
        if set(data) - allowed:
            raise ConfigError("配置含未知顶层字段。")
        kwargs: dict = {"base": p.parent}
        for group in ("feishu", "privacy", "runtime", "models"):
            for key, value in data.get(group, {}).items():
                if key in kwargs or key not in Config.__dataclass_fields__ or key in ("base", "sources"):
                    raise ConfigError("配置含重复或未知字段。")
                kwargs[key] = value
        kwargs["sources"] = tuple(Source(**item) for item in data.get("sources", []))
        cfg = Config(**kwargs)
        cfg.validate()
        load_env_file(p.parent / ".env")
        return cfg
    except (OSError, tomllib.TOMLDecodeError, TypeError, ValueError) as exc:
        raise ConfigError("配置文件格式或字段类型错误。") from None
