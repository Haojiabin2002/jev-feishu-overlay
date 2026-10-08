#!/usr/bin/env python3
"""Create a local .app launcher, NOT a self-contained or notarized binary."""
from __future__ import annotations
import argparse
from pathlib import Path
import plistlib
import shlex
import subprocess
import tomllib

ROOT = Path(__file__).resolve().parent.parent
VERSION = tomllib.loads((ROOT/'pyproject.toml').read_text())['project']['version']

def build(root: Path) -> Path:
    app = root/"dist/Jev 飞书悬浮助手.app"
    if app.exists():
        raise FileExistsError("启动器已存在；核实后手工移除再重新生成，不自动覆盖。")
    contents=app/"Contents"; executable=contents/"MacOS/launch"
    executable.parent.mkdir(parents=True)
    resources=contents/'Resources';resources.mkdir()
    bootstrap=resources/'launcher.zsh'
    bootstrap.write_text("#!/bin/zsh\nset -euo pipefail\ncd "+shlex.quote(str(root))+"\nexport PATH=\"$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH\"\nexec .venv/bin/python -m jev_feishu --config "+shlex.quote(str(root/"config.toml"))+" overlay\n",encoding='utf-8')
    bootstrap.chmod(0o700)
    # Keep the upstream bundle-owned parent alive; do not run its dependency installer.
    subprocess.run(['xcrun','clang','-std=c11','-Os','-Wall','-Wextra','-Werror',
                    str(ROOT/'vendor/jev_jarvis/launcher.c'),'-o',str(executable)],check=True,capture_output=True)
    executable.chmod(0o700)
    info={"CFBundleName":"Jev 飞书悬浮助手", "CFBundleDisplayName":"Jev 飞书悬浮助手",
          "CFBundleIdentifier":"local.jev.feishu.overlay", "CFBundleVersion":VERSION,
          "CFBundleShortVersionString":VERSION, "CFBundleExecutable":"launch",
          "CFBundlePackageType":"APPL", "LSUIElement":True, "NSHighResolutionCapable":True}
    (contents/"Info.plist").write_bytes(plistlib.dumps(info))
    (resources/'UPSTREAM-LICENSE.txt').write_bytes((ROOT/'vendor/jev_jarvis/LICENSE').read_bytes())
    return app

def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    if not (ROOT/".venv/bin/python").exists():
        raise SystemExit("请先初始化项目虚拟环境。")
    try: path=build(ROOT)
    except FileExistsError as exc: raise SystemExit(str(exc))
    print(path)
    print("这是本机源码目录的启动器。不能删除或移动项目目录；未签名、公证或打包 Python。")

if __name__=="__main__":main()
