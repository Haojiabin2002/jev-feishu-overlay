#!/usr/bin/env python3
"""Install/uninstall a per-user macOS LaunchAgent. Never writes secrets into plist."""
from pathlib import Path
import argparse
import os
import plistlib
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from jev_feishu.config import load_config
from jev_feishu.binding import resolve_binding

LABEL = "local.jev-feishu-copilot"
p = argparse.ArgumentParser()
p.add_argument("action", choices=("install", "uninstall", "status", "render"))
p.add_argument("--config", default=str(ROOT / "config.toml"))
args = p.parse_args()
plist_path = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
domain = f"gui/{os.getuid()}"
if sys.platform != "darwin" and args.action != "render":
    raise SystemExit("launchd 管理仅适用于 macOS；Linux 环境不执行安装。")
if args.action == "status":
    raise SystemExit(subprocess.run(["launchctl", "print", f"{domain}/{LABEL}"], check=False).returncode)
if args.action == "uninstall":
    if plist_path.exists():
        subprocess.run(["launchctl", "bootout", domain, str(plist_path)], check=False)
        plist_path.unlink()
    print("LaunchAgent 已移除；本地配置、模型密钥和状态未删除。")
    raise SystemExit(0)
cfg = resolve_binding(load_config(args.config))
cfg.validate(live=True, require_inbox=True)
cfg.check_models()
env_file = cfg.base / ".env"
if not env_file.exists():
    raise SystemExit("后台服务不能依赖交互终端里的临时变量；请先安全配置本项目 .env。")
local_values = {}
for line in env_file.read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.lstrip().startswith("#"):
        key, value = line.split("=", 1)
        local_values[key.strip()] = value.strip().strip(chr(34)).strip(chr(39))
if any(not local_values.get(key) for key in (cfg.jev_key_env, cfg.llm_key_env)):
    raise SystemExit(".env 缺少模型密钥；launchd 不会复制终端环境中的秘密变量。")
if not cfg.allow_bot_send:
    raise SystemExit("请先完成发送同意与前台验收。")
python = ROOT / ".venv/bin/python"
if not python.is_file():
    raise SystemExit("请先运行 scripts/bootstrap.sh。")
cli = shutil.which(cfg.cli)
if not cli or not Path(cfg.cli).is_absolute():
    raise SystemExit("请先在 config.toml 中将 cli 设置为已有飞书 CLI 的绝对路径。")
cfg.state_dir.mkdir(mode=0o700, exist_ok=True)
os.chmod(cfg.state_dir, 0o700)
value = {
    "Label": LABEL,
    "ProgramArguments": [str(python), "-m", "jev_feishu", "--config", str(Path(args.config).resolve()), "run"],
    "WorkingDirectory": str(ROOT),
    "EnvironmentVariables": {"HOME": str(Path.home()), "PATH": str(Path(cli).parent)+":"+os.environ.get("PATH", "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"), "PYTHONUNBUFFERED": "1"},
    "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 60,
    "StandardOutPath": str(cfg.state_dir / "service.log"),
    "StandardErrorPath": str(cfg.state_dir / "service-error.log"),
    "ProcessType": "Background", "Umask": 0o077,
}
# Model secrets are loaded at runtime from .env, not copied into the plist.
if args.action == "render":
    print(plistlib.dumps(value).decode())
else:
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    if plist_path.exists():
        raise SystemExit("已有同名 LaunchAgent。先检查其状态并明确卸载后再安装，避免覆盖未知服务。")
    fd = os.open(plist_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(plistlib.dumps(value))
    try:
        subprocess.run(["launchctl", "bootstrap", domain, str(plist_path)], check=True)
    except subprocess.CalledProcessError:
        plist_path.unlink(missing_ok=True)
        raise SystemExit("launchctl bootstrap 失败，已移除本次 plist；请检查本机 GUI 会话和路径。")
    print("LaunchAgent 已提交。仍需检查 status 和在飞书发送“状态”完成验收。")
