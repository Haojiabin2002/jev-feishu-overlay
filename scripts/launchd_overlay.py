#!/usr/bin/env python3
"""Opt-in user login item; no sudo, no automatic restart on explicit quit."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import plistlib
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
LABEL = "local.jev-feishu-overlay"

def make_plist(root: Path, home: Path) -> dict:
    return {
        "Label": LABEL,
        "ProgramArguments": [str(root / ".venv/bin/python"), "-m", "jev_feishu",
                             "--config", str(root / "config.toml"), "overlay"],
        "WorkingDirectory": str(root),
        "RunAtLoad": True,
        "KeepAlive": False,
        "ProcessType": "Interactive",
        "LimitLoadToSessionType": "Aqua",
        "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"},
        "StandardOutPath": str(root / ".state/overlay.stdout.log"),
        "StandardErrorPath": str(root / ".state/overlay.stderr.log"),
    }

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["preview", "install", "status", "uninstall"])
    args = parser.parse_args()
    if args.action == "preview":
        print(plistlib.dumps(make_plist(ROOT, Path.home())).decode())
        return 0
    if sys.platform != "darwin":
        parser.error("此操作只能在用户 Mac 的桌面会话中执行。")
    dest = Path.home()/"Library/LaunchAgents"/(LABEL+".plist")
    domain = f"gui/{os.getuid()}"
    if args.action == "status":
        return subprocess.run(["launchctl", "print", domain+"/"+LABEL], check=False).returncode
    if args.action == "uninstall":
        if not dest.exists():
            print("没有本项目的登录项；未更改其他服务。")
            return 0
        # Only remove our own exact known label. This does not delete config or state.
        saved = plistlib.loads(dest.read_bytes())
        if saved.get("Label") != LABEL:
            parser.error("文件 Label 不符，拒绝移除。")
        subprocess.run(["launchctl", "bootout", domain, str(dest)], check=False)
        dest.unlink()
        print("已移除本项目登录项；保留配置与状态。")
        return 0
    if dest.exists():
        parser.error("登录项已经存在；先核实来源并卸载，不能静默覆盖。")
    if not (ROOT/".venv/bin/python").exists() or not (ROOT/"config.toml").exists():
        parser.error("先完成初始化与前台真实验收。")
    state = ROOT/".state"; state.mkdir(mode=0o700, exist_ok=True)
    state.chmod(0o700)
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.umask(0o077)
    data = plistlib.dumps(make_plist(ROOT, Path.home()))
    with dest.open("xb") as fp: fp.write(data)
    dest.chmod(0o600)
    cp = subprocess.run(["launchctl", "bootstrap", domain, str(dest)], capture_output=True, text=True)
    if cp.returncode:
        print("登录项文件已创建，但 launchctl 启动失败。请本机诊断；不能宣称部署完成。", file=sys.stderr)
        return 1
    print("登录项已注册；请核实桌面真的出现悬浮窗并完成一次分析。")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
