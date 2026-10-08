#!/usr/bin/env python3
"""Run interactively in a local terminal. Values are not echoed or passed as argv."""
from pathlib import Path
import getpass
import os
import sys
import argparse

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--jev-only', action='store_true', help='Codex SDK 模式，仅更新 Jev 密钥')
args = parser.parse_args()

if not sys.stdin.isatty():
    raise SystemExit("请在本机交互终端运行；不要通过聊天或命令行参数传递密钥。")
root = Path(__file__).resolve().parents[1]
path = root / ".env"
print("只写入本项目 .env；不读取或修改飞书 CLI 的凭据。")
jev = getpass.getpass("Jev API Key（隐藏输入）：").strip()
llm = jev if args.jev_only else getpass.getpass("生成模型 API Key（同一个 OpenRouter Key 时可留空）：").strip() or jev
if not jev or not llm or any("\n" in x or "\r" in x for x in (jev, llm)):
    raise SystemExit("密钥为空或格式无效，没有写入。")
if path.is_symlink():
    raise SystemExit(".env 是符号链接，未写入。")
updates = {'JEV_API_KEY': jev}
if not args.jev_only:
    updates['LLM_API_KEY'] = llm
lines = path.read_text(encoding='utf-8').splitlines() if path.exists() else []
if any(line.partition('=')[0].strip() in updates and line.partition('=')[2].strip() for line in lines):
    answer = input("将更新本项目已有模型密钥（其他配置保留），确认请输入 yes：").strip()
    if answer != "yes":
        raise SystemExit("未写入。")
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as f:
    for line in lines:
        if line.partition('=')[0].strip() not in updates:
            f.write(line + '\n')
    for key, value in updates.items():
        f.write(key + '=' + value + '\n')
os.chmod(path, 0o600)
print("已写入权限为 600 的 .env。没有显示密钥。")
