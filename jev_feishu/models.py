from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable
import copy
import json
import math
import os
import random
import ssl
import time
import urllib.error
import urllib.request

from .config import Config
from . import __version__
from .errors import ContractError, CopilotError, ProviderError
from .questions import GENERATION_SYSTEM, judge_questions, ranking_questions

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

class JsonTransport:
    """HTTPS, certificate validation, bounded retries; no response bodies in errors."""
    def __init__(self, timeout: float = 25, *, opener=None, sleeper: Callable = time.sleep):
        self.timeout = timeout
        self.opener = opener or urllib.request.build_opener(
            _NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))
        self.sleeper = sleeper

    def post(self, endpoint: str, key: str, payload: dict) -> dict:
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        for attempt in range(2):
            request = urllib.request.Request(endpoint, data=encoded, method="POST", headers={
                "Authorization": f"Bearer {key}", "Content-Type": "application/json",
                "User-Agent": f"jev-feishu-overlay/{__version__}", "X-Title": "Private Feishu Copilot",
            })
            try:
                with self.opener.open(request, timeout=self.timeout) as response:
                    raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise ContractError("模型响应超过安全上限。")
                obj = json.loads(raw.decode("utf-8"))
                if not isinstance(obj, dict):
                    raise ContractError("模型响应不是 JSON 对象。")
                if "error" in obj:
                    raise ProviderError("模型服务返回错误对象；请在本机核对服务状态与模型权限。")
                return obj
            except urllib.error.HTTPError as exc:
                status = exc.code
                retry_after = exc.headers.get("Retry-After", "") if exc.headers else ""
                exc.close()
                if attempt == 0 and status in (429, 500, 502, 503, 504, 529):
                    try:
                        delay = min(5.0, max(0.5, float(retry_after)))
                    except (ValueError, TypeError):
                        delay = 0.7 + random.random() * 0.3
                    self.sleeper(delay)
                    continue
                hints = {401: "密钥无效", 403: "账户或模型权限不足", 404: "端点或模型不可用", 422: "请求契约不符"}
                raise ProviderError(f"模型服务 HTTP {status}：{hints.get(status, '调用失败')}。") from None
            except (urllib.error.URLError, TimeoutError, OSError):
                if attempt == 0:
                    self.sleeper(0.7)
                    continue
                raise ProviderError("模型网络/TLS/超时错误；不会关闭证书验证或改投其他服务。") from None
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise ContractError("模型服务未返回有效 UTF-8 JSON。") from None
        raise ProviderError("模型调用失败。")

def _number(value: Any, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError("模型数值字段类型错误。")
    v = float(value)
    if not math.isfinite(v) or not low <= v <= high:
        raise ContractError("模型数值字段超出合法范围。")
    return v

def validate_answers(response: dict, questions: dict) -> dict:
    answers = response.get("answers")
    if not isinstance(answers, dict) or any(key not in answers for key in questions):
        raise ContractError("Jev 缺少必要的结构化答案；不以默认分数伪装成功。")
    for key, question in questions.items():
        a = answers[key]
        if not isinstance(a, dict) or a.get("type") != question["type"]:
            raise ContractError("Jev 答案类型与问题不符。")
        if question["type"] == "noul":
            _number(a.get("noul"), 0, 1)
            continue
        _number(a.get("confidence"), 0, 1)
        expected = set(question["criteria"]) if question["type"] == "choice" else {str(i) for i in range(len(question["criteria"]))}
        probs = a.get("probabilities")
        if not isinstance(probs, dict) or set(probs) != expected:
            raise ContractError("Jev 概率分布缺项或有未知选项。")
        for probability in probs.values():
            _number(probability, 0, 1)
        if abs(sum(probs.values()) - 1) > 0.02:
            raise ContractError("Jev 概率分布总和异常。")
        if question["type"] == "choice":
            selected = a.get("choice")
            if selected not in expected or probs[selected] < max(probs.values()) - 1e-5:
                raise ContractError("Jev choice 与概率分布不一致。")
        else:
            _number(a.get("score"), 0, len(expected)-1)
            if abs(a["score"] - sum(int(k)*v for k, v in probs.items())) > 0.05:
                raise ContractError("Jev Score 与概率分布不一致。")
    return {key: answers[key] for key in questions}

def parse_candidates(response: dict) -> list[str]:
    try:
        text = response["choices"][0]["message"]["content"]
        if not isinstance(text, str) or len(text) > 12000:
            raise ValueError()
        text = text.strip()
        if text.startswith("```") and text.endswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        decoded = json.loads(text)
        values = decoded.get("replies") if isinstance(decoded, dict) else decoded
        if not isinstance(values, list) or len(values) != 3:
            raise ValueError()
        if any(not isinstance(v, str) or not 1 <= len(v.strip()) <= 120 for v in values):
            raise ValueError()
        replies = [v.strip() for v in values]
        if len(set(replies)) != 3:
            raise ValueError()
        return replies
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
        raise ContractError("生成模型未返回三条有效且不同的短回复；没有用固定套话补齐。") from None

@dataclass
class AnalysisResult:
    state: dict
    judgement: dict = field(default_factory=dict)
    candidates: list[str] = field(default_factory=list)
    ranking: dict = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    timings_ms: dict[str, int] = field(default_factory=dict)
    usage: dict[str, dict] = field(default_factory=dict)
    demo: bool = False

class ModelClient:
    def __init__(self, cfg: Config, transport=None):
        self.cfg = cfg
        self.transport = transport or JsonTransport(cfg.model_timeout)

    def judge(self, state: dict) -> tuple[dict, dict]:
        return self._jev(state, judge_questions())

    def rank(self, state: dict, candidates: list[str]) -> tuple[dict, dict]:
        expanded = {**state, "candidates": {f"reply_{i+1}": text for i, text in enumerate(candidates)}}
        return self._jev(expanded, ranking_questions(candidates))

    def _jev(self, state: dict, questions: dict) -> tuple[dict, dict]:
        response = self.transport.post(self.cfg.jev_url, os.environ.get(self.cfg.jev_key_env, ""),
                                       {"model": self.cfg.jev_model, "state": state, "questions": questions})
        return validate_answers(response, questions), safe_usage(response.get("usage", {}))

    def generate(self, state: dict) -> tuple[list[str], dict]:
        if self.cfg.llm_backend == "codex_sdk":
            from .codex_backend import generate
            response = generate(self.cfg, state, GENERATION_SYSTEM)
            wrapped = {"choices": [{"message": {"content": json.dumps({"replies": response.get("replies")})}}]}
            return parse_candidates(wrapped), safe_usage(response.get("usage", {}))
        payload = {
            "model": self.cfg.llm_model,
            "messages": [{"role": "system", "content": GENERATION_SYSTEM},
                         {"role": "user", "content": json.dumps(state, ensure_ascii=False)}],
            "temperature": 0.4, "max_tokens": 800,
        }
        if self.cfg.json_mode:
            payload["response_format"] = {"type": "json_object"}
        response = self.transport.post(self.cfg.llm_url, os.environ.get(self.cfg.llm_key_env, ""), payload)
        return parse_candidates(response), safe_usage(response.get("usage", {}))

def safe_usage(obj: Any) -> dict:
    if not isinstance(obj, dict):
        return {}
    return {k: v for k, v in obj.items() if k in ("prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens", "cost")
            and isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0}

class Analyzer:
    def __init__(self, cfg: Config, client=None, *, demo: bool = False):
        self.cfg, self.demo = cfg, demo
        self.client = client or ModelClient(cfg)

    def analyze(self, state: dict, *, on_update=None, cancelled=None) -> AnalysisResult:
        """Progress contains completed stages, never fabricated model probabilities.

        on_update(stage, status, snapshot) is called on the calling worker thread.
        The UI must marshal events to its main loop; snapshots do not share mutations.
        Cancellation cannot revoke HTTPS already sent; it prevents later ranking.
        """
        cancelled = cancelled or (lambda: False)
        if not self.demo:
            self.cfg.check_models()
        started = time.monotonic()
        result = AnalysisResult(state=state, demo=self.demo)
        def emit(stage, status):
            if on_update is not None and not cancelled():
                on_update(stage, status, copy.deepcopy(result))
        def timed(fn, *args):
            begin = time.monotonic()
            value, usage = fn(*args)
            return value, usage, int((time.monotonic()-begin)*1000)
        if cancelled():
            return result
        # Independent fan-out: drafts do not pretend to have read the first judgement.
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="model") as pool:
            tasks = {pool.submit(timed, self.client.judge, state): "judgement",
                     pool.submit(timed, self.client.generate, state): "candidates"}
            emit("judgement", "running")
            emit("candidates", "running")
            for future in as_completed(tasks):
                name = tasks[future]
                try:
                    value, usage, elapsed = future.result()
                    setattr(result, name, value)
                    result.usage[name] = usage
                    result.timings_ms[name] = elapsed
                except CopilotError as exc:
                    result.errors[name] = str(exc)
                except Exception:
                    result.errors[name] = "内部解析错误，未输出原始响应。"
                emit(name, "failed" if name in result.errors else "done")
        if cancelled():
            result.errors["cancelled"] = "已停止后续排序；已发出的模型请求无法撤回。"
        elif result.candidates:
            emit("ranking", "running")
            try:
                ranking, usage, elapsed = timed(self.client.rank, state, result.candidates)
                result.ranking = ranking
                result.usage["ranking"] = usage
                result.timings_ms["ranking"] = elapsed
            except CopilotError as exc:
                result.errors["ranking"] = str(exc)
            except Exception:
                result.errors["ranking"] = "排序内部错误，未输出原始响应。"
            emit("ranking", "failed" if "ranking" in result.errors else "done")
        else:
            emit("ranking", "skipped")
        result.timings_ms["total"] = int((time.monotonic()-started)*1000)
        return result
