"""One-shot Codex SDK generation; no transcript in argv or application logs."""
from pathlib import Path
import json
import os
import signal
import shutil
import subprocess
from .errors import ProviderError, ContractError


def check_runtime(cfg):
    for name,command in (('Codex',cfg.codex_binary),('Node',cfg.node_binary)):
        if not shutil.which(command):
            raise ProviderError(f'Codex SDK 无法启动：配置的 {name} 程序不存在或不可执行，请更新本项目程序路径。')


def run_process(argv, *, input, env, timeout, **kwargs):
    # A separate group lets timeout terminate both Node and its Codex child.
    with subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, env=env,
                          start_new_session=True, **kwargs) as child:
        try:
            stdout, stderr = child.communicate(input, timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            child.communicate()
            raise
        return subprocess.CompletedProcess(argv, child.returncode, stdout, stderr)


def generate(cfg, state, instructions):
    check_runtime(cfg)
    runner = Path(__file__).resolve().parents[1] / 'sdk/generate.mjs'
    # Do not pass model or unrelated service secrets into the SDK runtime.
    env = {k: v for k, v in os.environ.items()
           if k in ('HOME', 'PATH', 'TMPDIR', 'LANG', 'LC_ALL', 'CODEX_HOME', 'SSL_CERT_FILE', 'SSL_CERT_DIR')}
    body = {'state': state, 'instructions': instructions, 'codex_binary': cfg.codex_binary,
            'model': cfg.codex_model, 'effort': cfg.codex_reasoning_effort,
            'timeout_ms': int(cfg.model_timeout * 1000)}
    try:
        result = run_process([cfg.node_binary, str(runner)], input=json.dumps(body, ensure_ascii=False),
                             env=env, timeout=cfg.model_timeout + 5, shell=False)
    except subprocess.TimeoutExpired:
        raise ProviderError('Codex SDK 生成超时；未重试，请稍后重新分析。') from None
    except OSError:
        raise ProviderError('Codex SDK 无法启动，请检查本机 Node、SDK 和 Codex 路径。') from None
    if result.returncode:
        raise ProviderError('Codex SDK 生成失败，请在本机检查 Codex 登录与模型可用性；未切换服务。')
    try:
        if len(result.stdout) > 16000:
            raise ValueError()
        value = json.loads(result.stdout)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, TypeError):
        raise ContractError('Codex SDK 返回无效数据；未使用示例回复。') from None
