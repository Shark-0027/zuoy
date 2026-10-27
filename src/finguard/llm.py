from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx

from finguard.config import Settings

LLM_DATA_SCOPE = (
    "模型只接收结构化指标方向、规则状态、限制与待核验事项；"
    "不发送原始上传文件全文、逐行科目明细、个人信息或 API 密钥。"
)

LOCAL_NARRATIVE = (
    "本期收入增长没有转化为利润与现金流的同步改善：成本增速高于收入，毛利率收缩；"
    "经营现金流与净利润的比值低于阈值，但仅凭当前一次同比比较，尚不足以证明连续两期触发。"
    "应收账款、其他应收款与营运资本占用需要结合账龄、对手方和回款资料核验。"
    "以上结论只描述当前数据集可观察到的事实，不构成舞弊、违法或持续经营判断。"
)

BANNED_PHRASES = ("舞弊", "造假", "违法行为", "投资建议", "审计意见")
INSIGHT_STATUSES = {"remote_success", "remote_failed_fallback", "remote_rejected_fallback"}


def sanitize_error(message: str, settings: Settings) -> str:
    text = str(message)
    for secret in (settings.llm_api_key, settings.llm_base_url):
        if secret:
            text = text.replace(secret, "[redacted]")
    return text[:300]


def describe_llm_error(exc: Exception, settings: Settings) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        response = exc.response
        status = response.status_code
        reason = response.reason_phrase
        detail = ""
        try:
            body = response.json()
            if isinstance(body, dict):
                detail = str(
                    body.get("error", {}).get("message", "")
                    if isinstance(body.get("error"), dict)
                    else body.get("message", "")
                    or body.get("detail", "")
                    or body.get("error", "")
                )
        except Exception:  # noqa: BLE001 - response bodies are best-effort diagnostics
            detail = response.text[:120]
        reason_map = {
            401: "认证失败或 API Key 无效",
            403: "模型服务拒绝访问或无权限",
            404: "接口路径不存在；请检查 LLM_BASE_URL 或设置 LLM_COMPLETIONS_PATH",
            429: "模型限流或配额不足",
        }
        reason_text = reason_map.get(status, f"模型服务返回 {status} {reason}")
        if detail:
            return f"{reason_text}（{status}）：{sanitize_error(detail, settings)[:240]}"
        return f"{reason_text}（{status}）"
    if isinstance(exc, httpx.RequestError):
        return f"网络连接失败：{sanitize_error(exc, settings)[:240]}"
    return sanitize_error(exc, settings)[:300]


def _json_candidates(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if fenced:
        return [fenced.group(1), text]
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return [text[start : end + 1], text]
    return [text]


def parse_json_object(text: str) -> dict[str, Any]:
    errors: list[str] = []
    for candidate in _json_candidates(text):
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError as exc:
            errors.append(str(exc))
            continue
        if isinstance(value, dict):
            return value
        errors.append("模型返回的 JSON 不是对象")
    raise ValueError("模型未返回可解析的 JSON 对象：" + ("；".join(errors[:2]) or "空响应"))


def _clean_strings(values: Any, *, max_items: int, max_chars: int) -> list[str]:
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        item = value.strip()
        if item and item not in result:
            result.append(item[:max_chars])
        if len(result) >= max_items:
            break
    return result


def validate_insight_payload(payload: dict[str, Any]) -> dict[str, Any]:
    summary = payload.get("summary")
    if not isinstance(summary, str) or len(summary.strip()) < 20:
        raise ValueError("模型解读缺少有效的 summary")
    summary = summary.strip()[:600]
    signals = _clean_strings(payload.get("signals"), max_items=6, max_chars=240)
    questions = _clean_strings(payload.get("questions"), max_items=6, max_chars=240)
    limitations = _clean_strings(payload.get("limitations"), max_items=6, max_chars=240)
    if not signals:
        raise ValueError("模型解读缺少 signals")
    if not limitations:
        limitations = ["结论仅基于结构化摘要；需要业务资料和专业复核。"]

    joined = "\n".join([summary, *signals, *questions, *limitations])
    if re.search(r"\d", joined):
        raise ValueError("模型解读包含未经验证的数字；已拒绝该输出。")
    for phrase in BANNED_PHRASES:
        if phrase in joined:
            raise ValueError(f"模型解读包含禁止的定性表述：{phrase}")
    return {
        "summary": summary,
        "signals": signals,
        "questions": questions,
        "limitations": limitations,
    }


@dataclass(slots=True)
class NarrativeOutcome:
    text: str
    status: str
    error: str = ""


@dataclass(slots=True)
class InsightOutcome:
    payload: dict[str, Any] | None
    status: str
    error: str = ""


@dataclass(slots=True)
class ConnectionOutcome:
    ok: bool
    status: str
    model: str = ""
    latency_ms: int | None = None
    error: str = ""
    raw_text: str = field(default="", repr=False)


class NarrativeClient:
    """Small OpenAI-compatible client used for narrative work only.

    Financial arithmetic, thresholds and evidence selection stay in Python.
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def configured(self) -> bool:
        return self.settings.llm_configured

    def status(self) -> dict[str, Any]:
        host = ""
        if self.settings.llm_base_url:
            try:
                host = urlparse(self.settings.llm_base_url).netloc
            except ValueError:
                host = ""
        return {
            "configured": self.configured,
            "mode": "remote_llm" if self.configured else "local_deterministic",
            "model": self.settings.llm_model if self.configured else "",
            "base_host": host,
            "api_key_configured": bool(self.settings.llm_api_key),
            "timeout_seconds": self.settings.llm_timeout_seconds,
            "temperature": self.settings.llm_temperature,
            "max_tokens": self.settings.llm_max_tokens,
            "data_scope": LLM_DATA_SCOPE,
            "note": (
                "启用远程模型后，任务所需的结构化财务摘要会发送到配置的服务；"
                "原始文件不会发送。若调用失败，系统自动回退到本地规则叙述。"
                if self.configured
                else "未配置远程模型，当前完全在本地运行；财务计算、规则与报告均不依赖外部 API。"
            ),
        }

    def _endpoint(self) -> str:
        base = self.settings.llm_base_url.strip().rstrip("/")
        if base.endswith("/chat/completions"):
            return base

        # Explicit override for gateways that do not use /v1/chat/completions.
        override = self.settings.llm_completions_path.strip()
        if override:
            return base + (override if override.startswith("/") else "/" + override)

        path = urlparse(base).path.lower()
        if re.search(r"/v\d+(?:/|$)", path):
            return base + "/chat/completions"
        # Volcengine Ark coding gateways use /api/coding/v3/... rather than /v1/....
        if "ark" in urlparse(base).netloc.lower() and "/api/coding" in path:
            return base + "/v3/chat/completions"
        return base + "/v1/chat/completions"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.llm_api_key}",
            "Content-Type": "application/json",
        }

    def _post_chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        # Local model calls usually go direct. System proxy variables may point at
        # CLI-only proxies (for example socks://), so trust them only when asked.
        with httpx.Client(
            timeout=self.settings.llm_timeout_seconds,
            trust_env=self.settings.llm_trust_env,
        ) as client:
            response = client.post(self._endpoint(), headers=self._headers(), json=payload)
            response.raise_for_status()
            return response.json()

    @staticmethod
    def _message_text(body: dict[str, Any]) -> str:
        choice = body.get("choices", [{}])[0] if isinstance(body.get("choices"), list) else {}
        message = choice.get("message", {}) if isinstance(choice, dict) else {}
        content = message.get("content", "") if isinstance(message, dict) else ""
        if isinstance(content, list):
            # Some compatible gateways return content parts.
            content = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part) for part in content
            )
        return str(content).strip()

    def generate(
        self,
        *,
        metrics: list[dict[str, Any]],
        findings: list[dict[str, Any]],
        pending: list[dict[str, Any]],
    ) -> NarrativeOutcome:
        if not self.configured:
            return NarrativeOutcome(LOCAL_NARRATIVE, "local", "")

        payload = {
            "model": self.settings.llm_model,
            "temperature": self.settings.llm_temperature,
            "max_tokens": self.settings.llm_max_tokens,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是 FinGuard 的财务分析叙述助手。只根据提供的结构化结果解释关系、"
                        "不确定性和核查方向。禁止输出任何数字、金额、百分比、日期、文件名、"
                        "原始数据行或新事实；禁止作舞弊、违法、投资或审计结论。"
                        "用 2 段中文，总字数不超过 260 字。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "请生成财务诊断的定性叙述。\n"
                        f"指标：{metrics}\n"
                        f"规则发现：{findings}\n"
                        f"待核验：{pending}\n"
                        "只能使用上述摘要，不要添加数字。"
                    ),
                },
            ],
        }
        try:
            body = self._post_chat(payload)
            text = self._message_text(body)
            if not text:
                return NarrativeOutcome(LOCAL_NARRATIVE, "remote_failed_fallback", "模型返回空文本")
            if re.search(r"\d", text):
                return NarrativeOutcome(
                    LOCAL_NARRATIVE,
                    "remote_rejected_fallback",
                    "模型叙述包含未经结构化校验的数字，已回退到本地叙述。",
                )
            for phrase in BANNED_PHRASES:
                if phrase in text:
                    return NarrativeOutcome(
                        LOCAL_NARRATIVE,
                        "remote_rejected_fallback",
                        f"模型叙述包含禁止的定性表述：{phrase}",
                    )
            return NarrativeOutcome(text, "remote_success", "")
        except Exception as exc:  # noqa: BLE001 - errors are surfaced in sanitized form
            return NarrativeOutcome(LOCAL_NARRATIVE, "remote_failed_fallback", describe_llm_error(exc, self.settings))

    def generate_insight(
        self,
        *,
        metrics: list[dict[str, Any]],
        findings: list[dict[str, Any]],
        pending: list[dict[str, Any]],
    ) -> InsightOutcome:
        """Generate a validated qualitative interpretation.

        The request intentionally contains metric directions rather than raw
        values. The response is rejected if it contains digits, so the model
        cannot introduce an ungrounded financial number.
        """
        if not self.configured:
            return InsightOutcome(None, "local", "未配置远程模型。")

        payload = {
            "model": self.settings.llm_model,
            "temperature": self.settings.llm_temperature,
            "max_tokens": self.settings.llm_max_tokens,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是 FinGuard 的财务分析解读助手。只基于用户提供的结构化摘要，"
                        "解释业务含义、证据边界和下一步核查方向。"
                        "禁止输出数字、金额、百分比、日期、文件名、原始数据行或新事实；"
                        "禁止判断舞弊、违法或给出投资、审计结论。"
                        "只输出 UTF-8 JSON 对象，不要 Markdown 代码块，字段为："
                        '{"summary":"不超过180字的中文解读",'
                        '"signals":["最多6条关键信号"],'
                        '"questions":["最多6条待核验问题"],'
                        '"limitations":["最多6条限制"]}'
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "请生成 JSON 解读。只能引用输入中的方向、状态和限制。\n"
                        f"指标方向：{metrics}\n"
                        f"规则发现：{findings}\n"
                        f"待核验：{pending}"
                    ),
                },
            ],
        }
        try:
            body = self._post_chat(payload)
            text = self._message_text(body)
            if not text:
                return InsightOutcome(None, "remote_failed_fallback", "模型返回空文本")
            parsed = parse_json_object(text)
            validated = validate_insight_payload(parsed)
            return InsightOutcome(validated, "remote_success", "")
        except Exception as exc:  # noqa: BLE001
            return InsightOutcome(None, "remote_failed_fallback", describe_llm_error(exc, self.settings))

    def test_connection(self) -> ConnectionOutcome:
        """User-initiated provider test. It sends no financial data."""
        if not self.configured:
            return ConnectionOutcome(False, "not_configured", error="未配置远程模型。")
        started = time.perf_counter()
        payload = {
            "model": self.settings.llm_model,
            "temperature": 0,
            # Reasoning models may spend tokens before the visible answer; keep
            # the test small but large enough to leave content available.
            "max_tokens": 64,
            "messages": [
                {"role": "system", "content": "只回复两个字符：OK"},
                {"role": "user", "content": "连接测试"},
            ],
        }
        try:
            body = self._post_chat(payload)
            text = self._message_text(body)
            latency = int((time.perf_counter() - started) * 1000)
            if "OK" not in text.upper():
                return ConnectionOutcome(
                    False,
                    "unexpected_response",
                    model=self.settings.llm_model,
                    latency_ms=latency,
                    error=f"模型返回非预期内容：{text[:80]}",
                )
            return ConnectionOutcome(
                True,
                "success",
                model=self.settings.llm_model,
                latency_ms=latency,
                raw_text=text[:80],
            )
        except Exception as exc:  # noqa: BLE001
            return ConnectionOutcome(
                False,
                "failed",
                model=self.settings.llm_model,
                error=describe_llm_error(exc, self.settings),
            )
