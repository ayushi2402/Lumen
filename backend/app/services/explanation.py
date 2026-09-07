"""Explanations: deterministic first, LLM second.

Order of operations is the whole design:

1. The deterministic template explanation is computed **always**. It is stored
   on the event and is what the API returns by default.
2. Groq may then be asked to rephrase those same established facts more
   fluently. Its output is an enhancement layered on top, never a replacement
   for the record.

The LLM cannot influence anything that matters. It never sees a request to
judge significance, it is given only facts the engine already established, and
its output is validated before use. If Groq is missing, slow, erroring, or
returns something that breaks the rules, the deterministic text is served and
the product is unaffected.

Guardrails on LLM output, all enforced in code rather than by prompt alone:

* No advice vocabulary (buy / sell / hold / target price).
* No causal assertion unless the underlying evidence set explicitly
  established causation.
* No score, and no numbers beyond those supplied in the fact bundle.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings, get_settings
from app.intelligence.explain import explain, explain_score
from app.intelligence.models import AttentionResult, SignalType

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Words that must never appear in user-facing explanation text.
_BANNED_ADVICE = re.compile(
    r"\b(buy|sell|hold|accumulate|book profits?|target price|price target|"
    r"recommend(?:ation|ed|s)?|should (?:buy|sell|hold|consider)|"
    r"invest(?:ors? should)?)\b",
    re.IGNORECASE,
)

# Causal assertions, permitted only with explicit causal evidence.
_CAUSAL_CLAIM = re.compile(
    r"\b(because of|caused by|due to|as a result of|triggered by|led to|"
    r"resulted from|drove the|driven by)\b",
    re.IGNORECASE,
)

SYSTEM_PROMPT = (
    "You rewrite pre-computed market observations into one clear, neutral "
    "paragraph for a retail investor.\n"
    "STRICT RULES:\n"
    "1. Use ONLY the facts provided. Never add numbers, causes, or context.\n"
    "2. Never give investment advice. No buy, sell, hold, or price targets.\n"
    "3. Never claim one thing caused another unless the facts say causation "
    "was established. Otherwise say 'coincided with' or 'occurred alongside'.\n"
    "4. Never mention or invent a score.\n"
    "5. If confidence is not high, say the picture is incomplete.\n"
    "6. Two to four sentences. Plain language. No headings, no bullet points."
)


@dataclass(frozen=True)
class ExplanationResult:
    """What the API serves, and where each part came from."""

    text: str
    deterministic_text: str
    source: str  # "deterministic" | "llm"
    model: str | None = None
    fallback_reason: str | None = None

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "deterministic_text": self.deterministic_text,
            "source": self.source,
            "model": self.model,
            "fallback_reason": self.fallback_reason,
        }


def build_fact_bundle(result: AttentionResult) -> dict[str, Any]:
    """The only thing the LLM is ever shown.

    Contains established facts and no request for judgement. Note that the
    score is deliberately excluded - the model has no reason to see it and no
    permission to discuss it.
    """
    signals = [
        {
            "signal": s.signal_type.value,
            "statement": s.evidence,
            "value": s.value,
        }
        for s in result.signals
        if s.is_available and (s.normalized_score or 0.0) > 0.0
    ]
    news = next(
        (s for s in result.signals if s.signal_type is SignalType.NEWS_EVENT and s.is_available),
        None,
    )
    causal_established = bool(news.detail.get("causal_link_established")) if news else False

    return {
        "symbol": result.symbol,
        "direction": result.direction.value,
        "confidence": result.confidence.value,
        "established_facts": signals,
        "causation_established": causal_established,
        "missing_evidence": [
            s.signal_type.value for s in result.signals if not s.is_available
        ],
        "deterministic_summary": explain(result),
    }


def validate_llm_text(text: str, causation_established: bool) -> str | None:
    """Return a rejection reason, or ``None`` if the text is acceptable."""
    if not text or len(text.strip()) < 20:
        return "LLM returned an empty or too-short response."
    if _BANNED_ADVICE.search(text):
        return "LLM response contained investment advice."
    if not causation_established and _CAUSAL_CLAIM.search(text):
        return "LLM asserted causation that was not established."
    if re.search(r"\b(score|rating)\s*(?:of\s*)?\d", text, re.IGNORECASE):
        return "LLM referenced a score."
    return None


def explain_event(
    result: AttentionResult,
    use_llm: bool = True,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
) -> ExplanationResult:
    """Produce the best available explanation, degrading safely.

    The deterministic text is computed first and returned unless the LLM
    produces something that passes every guardrail.
    """
    settings = settings or get_settings()
    deterministic = explain(result)

    if not use_llm:
        return ExplanationResult(
            text=deterministic,
            deterministic_text=deterministic,
            source="deterministic",
            fallback_reason="LLM enhancement not requested.",
        )
    if not settings.has_groq:
        return ExplanationResult(
            text=deterministic,
            deterministic_text=deterministic,
            source="deterministic",
            fallback_reason="No Groq API key configured.",
        )

    bundle = build_fact_bundle(result)
    try:
        text = _call_groq(bundle, settings, client)
    except Exception as exc:  # network, timeout, malformed response
        return ExplanationResult(
            text=deterministic,
            deterministic_text=deterministic,
            source="deterministic",
            fallback_reason=f"Groq unavailable: {type(exc).__name__}",
        )

    rejection = validate_llm_text(text, bundle["causation_established"])
    if rejection:
        return ExplanationResult(
            text=deterministic,
            deterministic_text=deterministic,
            source="deterministic",
            fallback_reason=rejection,
        )

    return ExplanationResult(
        text=text.strip(),
        deterministic_text=deterministic,
        source="llm",
        model=settings.groq_model,
    )


def _call_groq(
    bundle: dict[str, Any], settings: Settings, client: httpx.Client | None = None
) -> str:
    """One chat completion. Raises on any failure; the caller falls back."""
    import json

    payload = {
        "model": settings.groq_model,
        "temperature": 0.2,
        "max_tokens": 260,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(bundle, default=str)},
        ],
    }
    owned = client is None
    http = client or httpx.Client(timeout=settings.groq_timeout_seconds)
    try:
        response = http.post(
            GROQ_URL,
            json=payload,
            headers={"Authorization": f"Bearer {settings.groq_api_key}"},
        )
        response.raise_for_status()
        data = response.json()
    finally:
        if owned:
            http.close()

    return data["choices"][0]["message"]["content"]


def score_rationale(result: AttentionResult) -> dict:
    """The "How LUMEN calculated this" payload.

    Always deterministic. This panel is the audit trail for a score, so an LLM
    must never be anywhere near it.
    """
    return {
        "summary": explain_score(result),
        "objective_score": result.objective_score,
        "personalization_adjustment": result.personalization_adjustment,
        "final_score": result.final_score,
        "severity": result.severity.value,
        "breakdown": result.breakdown,
        "confidence": result.confidence.value,
        "confidence_report": result.confidence_report.model_dump(mode="json"),
        "meaningful": result.meaningful,
        "signals": [
            {
                "type": s.signal_type.value,
                "family": s.family.value,
                "availability": s.availability.value,
                "value": s.value,
                "strength": s.normalized_score,
                "direction": s.direction.value,
                "evidence": s.evidence,
                "confidence": s.confidence,
            }
            for s in result.signals
        ],
    }
