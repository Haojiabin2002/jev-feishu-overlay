from __future__ import annotations
import argparse
from pathlib import Path
import os
import signal
import subprocess
import sys
import threading
import uuid

from . import __version__
from .binding import resolve_binding, save_binding
from .config import Config, load_config
from .demo import DemoClient, TRANSCRIPT
from .errors import CopilotError, ConfigError, PermissionDenied
from .lark import LarkCLI
from .messages import build_state, pasted_messages
from .models import Analyzer
from .render import render
from .service import Service
from .store import Store, instance_lock

BIND_MESSAGE = ("Jev 助手连接测试：仅在这个私聊向你反馈分析，不会给同事或业务群发送消息。"
                "部署完成后可以发送“帮助”。")

def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Jev 飞书悬浮助手（保留旧机器人兼容入口）")
    p.add_argument("--config", default="config.toml")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("demo", help="离线固定样例，不读取 CLI、不访问网络")
    overlay = sub.add_parser("overlay", help="本机悬浮窗；不向飞书发送报告")
    overlay.add_argument("--demo", action="store_true", help="合成数据演示，不访问飞书或模型")
    doctor = sub.add_parser("doctor", help="检查运行环境和 CLI 契约，不调用模型")
    doctor.add_argument("--live", action="store_true", help="额外验证当前飞书用户身份")
    doctor.add_argument("--overlay", action="store_true", help="检查原生悬浮窗与读取链路，不要求机器人发送")
    bind = sub.add_parser("bind", help="经确认向本人发送固定测试消息并绑定私聊")
    bind.add_argument("--send", action="store_true", help="同意由机器人向配置的本人发上述测试消息")
    sub.add_parser("probe-models", help="以合成样例测试两个模型端点，会产生 API 请求")
    analyze = sub.add_parser("analyze", help="本机按需分析，报告只输出本机，不发送飞书")
    target = analyze.add_mutually_exclusive_group(required=True)
    target.add_argument("--alias")
    target.add_argument("--text-file", type=Path)
    run = sub.add_parser("run", help="运行私人机器人服务")
    run.add_argument("--once", action="store_true", help="仅执行一轮（首次运行只初始化游标）")
    return p

def doctor(cfg: Config, live: bool, *, overlay: bool=False) -> None:
    print(f"Python {sys.version_info.major}.{sys.version_info.minor}；配置解析通过。")
    lark = LarkCLI(cfg)
    binary = lark.executable()
    required = [
        (["im", "+messages-send", "--help"], ["--user-id", "--idempotency-key", "--text", "--as"]),
        (["api", "--help"], ["--as", "--params", "--data"]),
    ]
    if overlay:
        import AppKit
        required = [(args, flags) for args, flags in required if args[0] == "api"]
        print("AppKit 原生面板可导入；前台交互仍需实际验证。")
    for args, flags in required:
        try:
            cp = subprocess.run([binary, *args], capture_output=True, text=True, timeout=15, shell=False)
        except (OSError, subprocess.TimeoutExpired):
            raise ConfigError("本地 CLI 帮助检查失败。") from None
        text = cp.stdout + cp.stderr
        if cp.returncode or any(flag not in text for flag in flags):
            raise ConfigError("已有 CLI 缺少所需参数；请让 Codex 核对本机 help 并适配，不能自动覆盖现有 CLI。")
    print("CLI 必要命令和参数可用。未打印任何授权资料。")
    print(f"云端内容处理同意：{cfg.allow_cloud}；机器人私聊发送同意：{cfg.allow_bot_send}。")
    keys = (cfg.jev_key_env,) if cfg.llm_backend == "codex_sdk" else (cfg.jev_key_env, cfg.llm_key_env)
    print("模型密钥状态：" + ", ".join(f"{key}={'已设置' if os.environ.get(key) else '未设置'}" for key in keys))
    if cfg.llm_backend == "codex_sdk":
        from .codex_backend import check_runtime
        check_runtime(cfg)
        print("Codex 与 Node 配置路径可执行。")
        print("生成后端：Codex SDK；使用本机 Codex 登录，不要求 LLM 密钥。真实调用仍需单独验证。")
    if live:
        cfg.validate(live=True)
        lark.verify_owner()
        print("飞书当前登录身份与 owner_open_id 一致；本检查未读取会话、调用模型或发送消息。")

def main(argv=None) -> int:
    args = parser().parse_args(argv)
    if args.command == "overlay":
        try:
            from .overlay.native_app import launch
        except ImportError:
            raise ConfigError("当前环境缺少原生界面依赖，请在本项目虚拟环境安装 requirements-native.txt。") from None
        return launch(args.config, demo=args.demo)
    if args.command == "demo":
        cfg = Config(base=Path.cwd(), owner_open_id="ou_demo_owner")
        state = build_state(pasted_messages(TRANSCRIPT, cfg.owner_open_id), cfg)
        print(render(Analyzer(cfg, DemoClient(), demo=True).analyze(state), cfg, "合成工作对话"))
        return 0
    cfg = load_config(args.config)
    if args.command == "doctor":
        doctor(cfg, args.live, overlay=args.overlay)
        return 0
    if args.command == "probe-models":
        cfg.check_models()
        # Synthetic sample only; does not read Feishu.
        sample_cfg = cfg if cfg.owner_open_id else Config(base=cfg.base, owner_open_id="ou_demo_owner")
        state = build_state(pasted_messages(TRANSCRIPT, sample_cfg.owner_open_id), sample_cfg)
        result = Analyzer(cfg).analyze(state)
        print(render(result, cfg, "合成样例端点测试，非真实聊天"))
        return 1 if result.errors else 0
    cfg.validate(live=True)
    lark = LarkCLI(cfg)
    if args.command == "bind":
        if not args.send:
            print("本命令会以机器人身份向配置的本人发送以下消息：\n" + BIND_MESSAGE)
            print("确认收件人、机器人身份和内容后，执行 bind --send；本次没有发送。")
            return 0
        lark.verify_owner()
        response = lark.send_owner(BIND_MESSAGE, "jev_bind_" + uuid.uuid4().hex)
        save_binding(cfg, response)
        print("机器人私聊已按接口返回的 chat_id 绑定。请在飞书确认测试消息，然后继续验收。")
        return 0
    if args.command == "analyze":
        if args.alias:cfg=cfg.model_config_for(args.alias)
        cfg.check_models()
        source = cfg.source(args.alias) if args.alias else None
        if source:
            messages, more = lark.read_source(source.alias)
        else:
            if args.text_file.stat().st_size > 100_000:
                raise ConfigError("文本文件过大。")
            text = args.text_file.read_text(encoding="utf-8")
            messages = pasted_messages(text, cfg.owner_open_id, cfg.max_input_chars)
            more = False
        store = Store(cfg)
        try:
            store.consume_analysis()
            result = Analyzer(cfg).analyze(build_state(messages, cfg, source, older_available=more))
            print(render(result, cfg, source.alias if source else "本机文本"))
            return 1 if result.errors else 0
        finally:
            store.close()
    if args.command == "run":
        if not cfg.allow_bot_send:
            raise PermissionDenied("启动机器人服务前需明确允许仅向本人发送建议。")
        cfg.check_models()
        cfg = resolve_binding(cfg)
        cfg.validate(live=True, require_inbox=True)
        lark = LarkCLI(cfg)
        lark.verify_owner()
        with instance_lock(cfg):
            store = Store(cfg)
            service = Service(cfg, lark, Analyzer(cfg), store)
            try:
                if args.once:
                    print(service.once())
                else:
                    stop = threading.Event()
                    for sig in (signal.SIGINT, signal.SIGTERM):
                        signal.signal(sig, lambda *_: stop.set())
                    print("event=started mode=owner_private_bot", flush=True)
                    service.run(stop)
            finally:
                store.close()
        return 0
    return 2

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CopilotError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        raise SystemExit(1)
    except (OSError, ValueError):
        print("错误：本机文件或输入数据无效。未输出原始数据或凭据。", file=sys.stderr)
        raise SystemExit(1)
