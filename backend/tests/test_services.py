"""Service-layer tests: explanation guardrails, digest, retention, behaviour.

The explanation tests are the most important here. They assert that the LLM
layer cannot introduce advice, invented causation, or a score - and that every
failure mode falls back to the deterministic text rather than degrading the
product.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import httpx
import pytest

from app.config import Settings
from app.db.models import MarketObservationRow, User, utcnow
from app.intelligence.scoring import score_observation
from app.services import digest as digest_service
from app.services import retention
from app.services.explanation import (
    build_fact_bundle,
    explain_event,
    score_rationale,
    validate_llm_text,
)
from app.services.fixtures import (
    SCENARIO_1_CORROBORATED_DECLINE,
    SCENARIO_5_NO_VOLATILITY_HISTORY,
)

RESULT = score_observation(SCENARIO_1_CORROBORATED_DECLINE)


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _groq_reply(text: str):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})

    return handler


# ---------------------------------------------------------------------------
# Explanation: fallback behaviour
# ---------------------------------------------------------------------------


def test_without_groq_the_deterministic_explanation_is_used() -> None:
    result = explain_event(RESULT, settings=Settings(groq_api_key=None))
    assert result.source == "deterministic"
    assert result.text == result.deterministic_text
    assert "No Groq API key" in result.fallback_reason


def test_groq_failure_falls_back_without_breaking(  ) -> None:
    """A dead LLM must not degrade the product."""

    def explode(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    result = explain_event(
        RESULT,
        settings=Settings(groq_api_key="test-key-not-real"),
        client=_mock_client(explode),
    )
    assert result.source == "deterministic"
    assert result.text == result.deterministic_text
    assert "Groq unavailable" in result.fallback_reason


def test_groq_http_error_falls_back() -> None:
    def server_error(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    result = explain_event(
        RESULT,
        settings=Settings(groq_api_key="test-key-not-real"),
        client=_mock_client(server_error),
    )
    assert result.source == "deterministic"


def test_valid_llm_output_is_used_as_an_enhancement() -> None:
    good = (
        "RELIANCE fell 4.2% while the index was broadly flat, and traded at "
        "roughly 2.8 times its usual volume for this point of the session. "
        "The decline coincided with a regulatory review reported shortly "
        "beforehand, though no causal link has been established."
    )
    result = explain_event(
        RESULT,
        settings=Settings(groq_api_key="test-key-not-real"),
        client=_mock_client(_groq_reply(good)),
    )
    assert result.source == "llm"
    assert result.text == good
    # The deterministic text is retained regardless.
    assert result.deterministic_text


# ---------------------------------------------------------------------------
# Explanation: guardrails
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_text",
    [
        "RELIANCE fell 4.2%. Investors should buy the dip while it lasts here.",
        "RELIANCE fell 4.2%. Analysts recommend accumulating at these levels now.",
        "RELIANCE fell 4.2%, approaching a target price of 1100 rupees shortly.",
    ],
)
def test_llm_output_containing_advice_is_rejected(bad_text: str) -> None:
    result = explain_event(
        RESULT,
        settings=Settings(groq_api_key="test-key-not-real"),
        client=_mock_client(_groq_reply(bad_text)),
    )
    assert result.source == "deterministic"
    assert "advice" in result.fallback_reason


def test_llm_output_asserting_unestablished_causation_is_rejected() -> None:
    """Co-occurrence is not cause, and the LLM does not get to decide it is."""
    causal = (
        "RELIANCE fell 4.2% because of the regulatory review announced this "
        "morning, which drove heavy selling through the session."
    )
    result = explain_event(
        RESULT,
        settings=Settings(groq_api_key="test-key-not-real"),
        client=_mock_client(_groq_reply(causal)),
    )
    assert result.source == "deterministic"
    assert "causation" in result.fallback_reason


def test_llm_output_referencing_a_score_is_rejected() -> None:
    result = explain_event(
        RESULT,
        settings=Settings(groq_api_key="test-key-not-real"),
        client=_mock_client(_groq_reply("RELIANCE fell sharply with a score of 82 today.")),
    )
    assert result.source == "deterministic"
    assert "score" in result.fallback_reason


def test_empty_llm_output_is_rejected() -> None:
    result = explain_event(
        RESULT,
        settings=Settings(groq_api_key="test-key-not-real"),
        client=_mock_client(_groq_reply("   ")),
    )
    assert result.source == "deterministic"


def test_causal_language_is_allowed_only_with_established_causation() -> None:
    assert validate_llm_text("The move was caused by the announcement.", False) is not None
    assert validate_llm_text("The move was caused by the announcement.", True) is None


# ---------------------------------------------------------------------------
# Explanation: the LLM never sees anything it could misuse
# ---------------------------------------------------------------------------


def test_fact_bundle_excludes_the_score() -> None:
    """The model has no reason to see a score and no permission to discuss it."""
    bundle = build_fact_bundle(RESULT)
    serialized = str(bundle).lower()
    assert "objective_score" not in serialized
    assert "final_score" not in serialized
    assert str(RESULT.objective_score) not in serialized


def test_fact_bundle_reports_missing_evidence_and_causation_state() -> None:
    bundle = build_fact_bundle(score_observation(SCENARIO_5_NO_VOLATILITY_HISTORY))
    assert bundle["causation_established"] is False
    assert "volatility_anomaly" in bundle["missing_evidence"]
    assert bundle["deterministic_summary"]


def test_score_rationale_is_always_deterministic() -> None:
    """The "How LUMEN calculated this" panel is an audit trail, never LLM output."""
    rationale = score_rationale(RESULT)
    assert rationale["objective_score"] == RESULT.objective_score
    assert set(rationale["breakdown"]) == {
        "price_movement", "relative_performance", "volume", "volatility", "gap", "news",
    }
    assert rationale["signals"]
    assert rationale["confidence"] == RESULT.confidence.value


# ---------------------------------------------------------------------------
# Digest baselines
# ---------------------------------------------------------------------------


def _user(db, **kwargs) -> User:
    user = User(is_guest=True, display_name="T", **kwargs)
    db.add(user)
    db.flush()
    return user


def test_baseline_precedence_prefers_a_manual_mark(db) -> None:
    now = utcnow()
    user = _user(
        db,
        manual_baseline_at=now - timedelta(hours=1),
        last_digest_reviewed_at=now - timedelta(hours=2),
        previous_visit_at=now - timedelta(hours=3),
    )
    baseline = digest_service.resolve_baseline(user, now)
    assert baseline.source == digest_service.BaselineSource.MANUAL


def test_reviewed_digest_beats_a_mere_app_open(db) -> None:
    now = utcnow()
    user = _user(
        db,
        last_digest_reviewed_at=now - timedelta(hours=2),
        previous_visit_at=now - timedelta(hours=3),
    )
    assert digest_service.resolve_baseline(user, now).source == (
        digest_service.BaselineSource.REVIEWED_DIGEST
    )


def test_first_visit_anchors_to_the_previous_session(db) -> None:
    user = _user(db)
    baseline = digest_service.resolve_baseline(user, datetime(2026, 3, 12, 10, 0))
    assert baseline.source == digest_service.BaselineSource.FIRST_USE
    assert baseline.at < datetime(2026, 3, 12, 0, 0)


def test_rapid_reopens_do_not_move_the_baseline(db) -> None:
    """Refreshing is not leaving and coming back."""
    user = _user(db)
    start = datetime(2026, 3, 12, 10, 0)

    digest_service.mark_app_opened(db, user, start)
    first = digest_service.resolve_baseline(user, start).at

    digest_service.mark_app_opened(db, user, start + timedelta(minutes=2))
    second = digest_service.resolve_baseline(user, start + timedelta(minutes=2)).at

    assert first == second


def test_a_real_absence_moves_the_baseline_forward(db) -> None:
    user = _user(db)
    start = datetime(2026, 3, 12, 10, 0)
    digest_service.mark_app_opened(db, user, start)
    before = digest_service.resolve_baseline(user, start).at

    returned = start + timedelta(hours=6)
    digest_service.mark_app_opened(db, user, returned)
    after = digest_service.resolve_baseline(user, returned).at

    assert after > before
    assert after == start


def test_reviewing_supersedes_a_manual_baseline(db) -> None:
    user = _user(db, manual_baseline_at=utcnow() - timedelta(hours=5))
    digest_service.mark_digest_reviewed(db, user)
    assert user.manual_baseline_at is None
    assert digest_service.resolve_baseline(user).source == (
        digest_service.BaselineSource.REVIEWED_DIGEST
    )


def test_digest_summary_is_generated_not_hardcoded(db) -> None:
    user = _user(db)
    empty = digest_service.build_digest(db, user, [], now=utcnow())
    assert "Nothing meaningful changed" in empty.summary
    assert empty.top_events == []


def test_multi_session_absence_produces_day_drilldown(db) -> None:
    """Returning after a long weekend gets structure, not a flat list."""
    user = _user(db, previous_visit_at=datetime(2026, 3, 10, 10, 0))
    result = digest_service.build_digest(db, user, [], now=datetime(2026, 3, 13, 10, 0))

    assert result.is_multi_session is True
    assert result.sessions_missed >= 2
    assert [d.session_date for d in result.days] == [
        date(2026, 3, 11), date(2026, 3, 12), date(2026, 3, 13),
    ]


def test_short_absence_is_not_multi_session(db) -> None:
    user = _user(db, previous_visit_at=datetime(2026, 3, 12, 10, 0))
    result = digest_service.build_digest(db, user, [], now=datetime(2026, 3, 12, 15, 0))
    assert result.is_multi_session is False
    assert result.days == []


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------


def _observation(db, instrument_id: int, observed_at: datetime) -> MarketObservationRow:
    row = MarketObservationRow(
        instrument_id=instrument_id,
        observed_at=observed_at,
        session_date=observed_at.date(),
        scope="live",
        source="replay",
        freshness="live",
        price=100.0,
        previous_price=100.0,
    )
    db.add(row)
    return row


def test_observations_older_than_the_window_are_purged(db) -> None:
    from app.services import catalog

    instrument = catalog.get_by_symbol(db, "INFY")
    now = utcnow()
    _observation(db, instrument.id, now - timedelta(days=120))
    _observation(db, instrument.id, now - timedelta(days=95))
    _observation(db, instrument.id, now - timedelta(days=10))
    db.flush()

    purged = retention.purge_expired_observations(db, now=now)
    assert purged == 2

    remaining = retention.retention_report(db)
    assert remaining["observations"] == 1
    assert remaining["retention_days"] == 90


def test_replay_observations_can_be_dropped_by_scope(db) -> None:
    from app.services import catalog

    instrument = catalog.get_by_symbol(db, "INFY")
    row = _observation(db, instrument.id, utcnow())
    row.scope = "replay:crash_stable_market"
    db.flush()

    assert retention.purge_replay_scope(db, "replay:crash_stable_market") == 1
    assert retention.retention_report(db)["observations"] == 0
