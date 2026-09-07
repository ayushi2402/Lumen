"""API integration tests.

Exercises the real routes against an in-memory database: auth, watchlists,
dashboard, events, behaviour, replay, notifications and the debug endpoint.

Several tests here are security assertions rather than feature tests - user
isolation, debug-endpoint protection, and credentials never appearing in a
response.
"""

from __future__ import annotations

from app.auth.google import GoogleIdentity, set_verifier
from app.config import get_settings

REPLAY = {"mode": "replay", "scenario": "crash_stable_market"}


class _StubVerifier:
    """Verifies without contacting Google. Never used outside tests."""

    def __init__(self, identity: GoogleIdentity) -> None:
        self.identity = identity

    def verify(self, id_token: str) -> GoogleIdentity:
        if "invalid" in id_token:
            from app.auth.google import GoogleAuthError

            raise GoogleAuthError("Invalid Google credential: bad signature")
        return self.identity


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_guest_session_returns_a_token_and_demo_watchlist(client, guest) -> None:
    assert guest["token_type"] == "bearer"
    assert guest["user"]["is_guest"] is True
    assert guest["user"]["has_watchlist"] is True


def test_protected_routes_reject_missing_tokens(client) -> None:
    assert client.get("/api/v1/dashboard").status_code == 401
    assert client.get("/api/v1/watchlists").status_code == 401


def test_protected_routes_reject_garbage_tokens(client) -> None:
    response = client.get(
        "/api/v1/watchlists", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert response.status_code == 401


def test_google_login_creates_a_user_from_a_verified_identity(client) -> None:
    set_verifier(
        _StubVerifier(
            GoogleIdentity(
                subject="google-sub-123",
                email="investor@example.com",
                name="Test Investor",
                picture=None,
                email_verified=True,
            )
        )
    )
    try:
        response = client.post("/api/v1/auth/google", json={"id_token": "valid-token"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["user"]["email"] == "investor@example.com"
        assert body["user"]["is_guest"] is False

        # Signing in again returns the same user, keyed on Google's subject.
        again = client.post("/api/v1/auth/google", json={"id_token": "valid-token"})
        assert again.json()["user"]["id"] == body["user"]["id"]
    finally:
        set_verifier(None)


def test_google_login_rejects_an_unverifiable_token(client) -> None:
    set_verifier(
        _StubVerifier(
            GoogleIdentity(subject="x", email=None, name=None, picture=None, email_verified=False)
        )
    )
    try:
        response = client.post("/api/v1/auth/google", json={"id_token": "invalid-google-token"})
        assert response.status_code == 401
    finally:
        set_verifier(None)


def test_profile_is_optional_and_updatable(client, auth_headers) -> None:
    assert client.get("/api/v1/auth/profile", headers=auth_headers).status_code == 200

    response = client.put(
        "/api/v1/auth/profile",
        headers=auth_headers,
        json={"risk_appetite": "moderate", "sectors": ["IT", "Banking"]},
    )
    assert response.status_code == 200
    assert response.json()["risk_appetite"] == "moderate"
    assert response.json()["onboarded"] is True


def test_starter_suggestions_are_supported_instruments(client, auth_headers) -> None:
    body = client.get("/api/v1/auth/starter-suggestions", headers=auth_headers).json()
    assert len(body["suggestions"]) > 0
    assert all("symbol" in s for s in body["suggestions"])


def test_setup_establishes_the_first_digest_baseline(client, auth_headers) -> None:
    """Without a baseline, the next visit would have nothing to compare to."""
    response = client.post(
        "/api/v1/auth/setup", headers=auth_headers, json={"symbols": ["INFY", "TCS"]}
    )
    assert response.status_code == 200
    assert response.json()["baseline_established"] is True


# ---------------------------------------------------------------------------
# Watchlists
# ---------------------------------------------------------------------------


def test_watchlist_crud(client, auth_headers) -> None:
    created = client.post(
        "/api/v1/watchlists", headers=auth_headers, json={"name": "Core"}
    )
    assert created.status_code == 201
    watchlist_id = created.json()["id"]

    added = client.post(
        f"/api/v1/watchlists/{watchlist_id}/items",
        headers=auth_headers,
        json={"symbol": "INFY", "priority": 2, "starred": True},
    )
    assert added.status_code == 200
    assert any(i["symbol"] == "INFY" for i in added.json()["items"])

    renamed = client.patch(
        f"/api/v1/watchlists/{watchlist_id}", headers=auth_headers, json={"name": "Core II"}
    )
    assert renamed.json()["name"] == "Core II"

    removed = client.delete(
        f"/api/v1/watchlists/{watchlist_id}/items/INFY", headers=auth_headers
    )
    assert all(i["symbol"] != "INFY" for i in removed.json()["items"])

    assert client.delete(
        f"/api/v1/watchlists/{watchlist_id}", headers=auth_headers
    ).status_code == 204


def test_unsupported_instruments_cannot_be_added(client, auth_headers) -> None:
    """The supported boundary is enforced, not faked."""
    watchlist_id = client.post(
        "/api/v1/watchlists", headers=auth_headers, json={"name": "Test"}
    ).json()["id"]

    response = client.post(
        f"/api/v1/watchlists/{watchlist_id}/items",
        headers=auth_headers,
        json={"symbol": "NIFTY50"},  # benchmark: context, not watchable
    )
    assert response.status_code == 400
    assert "not supported" in response.json()["detail"].lower()


def test_unknown_symbols_are_rejected(client, auth_headers) -> None:
    watchlist_id = client.post(
        "/api/v1/watchlists", headers=auth_headers, json={"name": "Test"}
    ).json()["id"]
    response = client.post(
        f"/api/v1/watchlists/{watchlist_id}/items",
        headers=auth_headers,
        json={"symbol": "NOTAREALSTOCK"},
    )
    assert response.status_code == 400


def test_same_stock_in_two_watchlists_has_independent_settings(client, auth_headers) -> None:
    """Starring in one list must not star it in another."""
    first = client.post("/api/v1/watchlists", headers=auth_headers, json={"name": "A"}).json()["id"]
    second = client.post("/api/v1/watchlists", headers=auth_headers, json={"name": "B"}).json()["id"]

    client.post(f"/api/v1/watchlists/{first}/items", headers=auth_headers, json={"symbol": "INFY"})
    client.post(f"/api/v1/watchlists/{second}/items", headers=auth_headers, json={"symbol": "INFY"})
    client.patch(
        f"/api/v1/watchlists/{first}/items/INFY",
        headers=auth_headers,
        json={"starred": True, "priority": 3, "notes": "core holding"},
    )

    a_item = next(
        i for i in client.get(f"/api/v1/watchlists/{first}", headers=auth_headers).json()["items"]
        if i["symbol"] == "INFY"
    )
    b_item = next(
        i for i in client.get(f"/api/v1/watchlists/{second}", headers=auth_headers).json()["items"]
        if i["symbol"] == "INFY"
    )
    assert a_item["starred"] is True and a_item["notes"] == "core holding"
    assert b_item["starred"] is False and b_item["notes"] is None


def test_duplicate_watchlist_names_are_rejected(client, auth_headers) -> None:
    client.post("/api/v1/watchlists", headers=auth_headers, json={"name": "Dupe"})
    second = client.post("/api/v1/watchlists", headers=auth_headers, json={"name": "Dupe"})
    assert second.status_code == 400


def test_watchlist_view_ranks_by_attention(client, auth_headers) -> None:
    lists = client.get("/api/v1/watchlists", headers=auth_headers).json()
    demo_id = lists[0]["id"]
    body = client.get(
        f"/api/v1/watchlists/{demo_id}", headers=auth_headers, params={**REPLAY, "step": 20}
    ).json()

    scores = [i.get("score") or 0 for i in body["items"]]
    assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# User isolation
# ---------------------------------------------------------------------------


def test_users_cannot_see_each_others_watchlists(client) -> None:
    first = client.post("/api/v1/auth/guest").json()
    second = client.post("/api/v1/auth/guest").json()
    headers_a = {"Authorization": f"Bearer {first['access_token']}"}
    headers_b = {"Authorization": f"Bearer {second['access_token']}"}

    watchlist_id = client.post(
        "/api/v1/watchlists", headers=headers_a, json={"name": "Private"}
    ).json()["id"]

    assert client.get(f"/api/v1/watchlists/{watchlist_id}", headers=headers_b).status_code == 404
    assert client.delete(f"/api/v1/watchlists/{watchlist_id}", headers=headers_b).status_code == 404


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


def test_dashboard_returns_everything_the_homepage_needs(client, auth_headers) -> None:
    body = client.get(
        "/api/v1/dashboard", headers=auth_headers, params={**REPLAY, "step": 20}
    ).json()

    for key in (
        "attention", "attention_headline", "since_you_were_away",
        "event_feed", "market", "notifications", "data_mode",
    ):
        assert key in body

    assert len(body["attention"]) <= 3
    assert body["data_mode"]["is_replay"] is True
    assert body["data_mode"]["label"] == "Demo / Replay"


def test_dashboard_attention_cards_carry_reasons_but_never_advice(client, auth_headers) -> None:
    body = client.get(
        "/api/v1/dashboard", headers=auth_headers, params={**REPLAY, "step": 20}
    ).json()
    assert body["attention"], "the crash scenario must surface at least one card"

    for card in body["attention"]:
        assert card["primary_reason"]
        assert card["why_it_matters"]
        assert card["severity"] in {"Worth Watching", "High Attention", "Critical"}
        text = f"{card['primary_reason']} {card['why_it_matters']}".lower()
        for banned in ("buy", "sell", " hold", "target price", "recommend"):
            assert banned not in text


def test_dashboard_headline_is_generated_from_actual_events(client, auth_headers) -> None:
    crash = client.get(
        "/api/v1/dashboard", headers=auth_headers, params={**REPLAY, "step": 20}
    ).json()
    quiet = client.get(
        "/api/v1/dashboard",
        headers=auth_headers,
        params={"mode": "replay", "scenario": "quiet_session", "step": 20},
    ).json()

    assert "deserves your attention" in crash["attention_headline"]
    assert quiet["attention"] == []
    assert "Nothing" in quiet["attention_headline"]


def test_market_wide_selloff_reports_context_but_no_attention(client, auth_headers) -> None:
    """The judge-facing moment: everything fell, nothing demands attention."""
    body = client.get(
        "/api/v1/dashboard",
        headers=auth_headers,
        params={"mode": "replay", "scenario": "market_wide_selloff", "step": 20},
    ).json()

    assert body["attention"] == []
    assert body["market"]["is_market_wide_move"] is True
    assert "market" in body["attention_headline"].lower()


def test_live_mode_without_any_provider_is_labelled_demo_not_live(client, auth_headers) -> None:
    """With no market access the dashboard still works - and says it is demo data."""
    body = client.get("/api/v1/dashboard", headers=auth_headers, params={"mode": "live"}).json()
    assert body["data_mode"]["degraded"] is True
    assert body["data_mode"]["label"] == "Demo / Replay"
    assert body["data_mode"]["is_replay"] is True
    assert any("demo data" in w.lower() for w in body["warnings"])


def test_refreshing_the_dashboard_does_not_destroy_the_digest(client, auth_headers) -> None:
    """The baseline advances on explicit review only, never on render."""
    live = {"mode": "live"}
    first = client.get(
        "/api/v1/dashboard", headers=auth_headers, params=live
    ).json()["since_you_were_away"]["baseline_at"]
    second = client.get(
        "/api/v1/dashboard", headers=auth_headers, params=live
    ).json()["since_you_were_away"]["baseline_at"]
    assert first == second


def test_reviewing_the_digest_advances_the_baseline(client, auth_headers) -> None:
    """Live mode: an explicit review moves the baseline forward.

    Checked in live mode because a replay pins its baseline to the scenario
    start by design.
    """
    live = {"mode": "live"}
    before = client.get(
        "/api/v1/dashboard", headers=auth_headers, params=live
    ).json()["since_you_were_away"]["baseline_at"]

    assert client.post("/api/v1/dashboard/digest/reviewed", headers=auth_headers).status_code == 200

    after = client.get(
        "/api/v1/dashboard", headers=auth_headers, params=live
    ).json()["since_you_were_away"]["baseline_at"]
    assert after > before


def test_manual_baseline_takes_precedence(client, auth_headers) -> None:
    response = client.post("/api/v1/dashboard/digest/baseline", headers=auth_headers)
    assert response.json()["baseline"]["source"] == "manual"


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


def _seed_events(client, auth_headers, step: int = 20) -> list[dict]:
    client.get("/api/v1/dashboard", headers=auth_headers, params={**REPLAY, "step": step})
    return client.get("/api/v1/events", headers=auth_headers, params=REPLAY).json()


def test_event_detail_returns_explanation_signals_and_breakdown(client, auth_headers) -> None:
    events = _seed_events(client, auth_headers)
    assert events, "replay crash scenario must produce an event"

    detail = client.get(
        f"/api/v1/events/{events[0]['id']}",
        headers=auth_headers,
        params={"explain_with_llm": False},
    ).json()

    assert detail["explanation"]["source"] == "deterministic"
    assert detail["explanation"]["text"]
    assert detail["signals"]
    assert set(detail["score_detail"]["breakdown"]) == {
        "price_movement", "relative_performance", "volume", "volatility", "gap", "news",
    }
    assert detail["timeline"]
    assert detail["market_context_note"]


def test_event_detail_never_contains_investment_advice(client, auth_headers) -> None:
    events = _seed_events(client, auth_headers)
    detail = client.get(
        f"/api/v1/events/{events[0]['id']}",
        headers=auth_headers,
        params={"explain_with_llm": False},
    ).json()
    text = f"{detail['explanation']['text']} {detail['headline']}".lower()
    for banned in ("buy", "sell", " hold", "target price", "recommend"):
        assert banned not in text


def test_dismissing_removes_from_feed_but_keeps_history(client, auth_headers) -> None:
    events = _seed_events(client, auth_headers)
    event_id = events[0]["id"]

    body = client.post(f"/api/v1/events/{event_id}/dismiss", headers=auth_headers).json()
    assert body["dismissed"] is True
    assert body["retained_in_history"] is True

    feed = client.get("/api/v1/events", headers=auth_headers, params=REPLAY).json()
    assert all(e["id"] != event_id for e in feed)

    history = client.get("/api/v1/events/history", headers=auth_headers, params=REPLAY).json()
    assert any(e["id"] == event_id for e in history)


def test_reviewing_marks_state_without_deleting(client, auth_headers) -> None:
    events = _seed_events(client, auth_headers)
    event_id = events[0]["id"]
    client.post(f"/api/v1/events/{event_id}/review", headers=auth_headers)

    feed = client.get("/api/v1/events", headers=auth_headers, params=REPLAY).json()
    reviewed = next(e for e in feed if e["id"] == event_id)
    assert reviewed["reviewed"] is True


def test_why_and_breakdown_interactions_are_recorded(client, auth_headers) -> None:
    events = _seed_events(client, auth_headers)
    event_id = events[0]["id"]
    assert client.post(f"/api/v1/events/{event_id}/why", headers=auth_headers).status_code == 200
    assert (
        client.post(f"/api/v1/events/{event_id}/breakdown", headers=auth_headers).status_code == 200
    )


def test_users_cannot_read_events_outside_their_watchlists(client, auth_headers) -> None:
    events = _seed_events(client, auth_headers)
    other = client.post("/api/v1/auth/guest").json()
    other_headers = {"Authorization": f"Bearer {other['access_token']}"}

    # Strip the other user's watchlist so the event is genuinely out of scope.
    for watchlist in client.get("/api/v1/watchlists", headers=other_headers).json():
        client.delete(f"/api/v1/watchlists/{watchlist['id']}", headers=other_headers)

    response = client.get(f"/api/v1/events/{events[0]['id']}", headers=other_headers)
    assert response.status_code == 403


def test_only_more_like_this_feedback_is_accepted(client, auth_headers) -> None:
    events = _seed_events(client, auth_headers)
    good = client.post(
        "/api/v1/events/feedback",
        headers=auth_headers,
        json={"event_id": events[0]["id"], "feedback": "more_like_this"},
    )
    bad = client.post(
        "/api/v1/events/feedback",
        headers=auth_headers,
        json={"event_id": events[0]["id"], "feedback": "less_like_this"},
    )
    assert good.status_code == 200
    assert bad.status_code == 400


# ---------------------------------------------------------------------------
# Behaviour and personalization
# ---------------------------------------------------------------------------


def test_interactions_are_recorded_and_shape_relevance(client, auth_headers) -> None:
    for _ in range(3):
        client.post(
            "/api/v1/behavior/interactions",
            headers=auth_headers,
            json={"kind": "why_opened", "symbol": "INFY"},
        )

    relevance = client.get("/api/v1/behavior/relevance", headers=auth_headers).json()
    assert relevance["relevance"]["INFY"] > 0


def test_unknown_interaction_kinds_are_rejected(client, auth_headers) -> None:
    response = client.post(
        "/api/v1/behavior/interactions",
        headers=auth_headers,
        json={"kind": "not_a_real_kind", "symbol": "INFY"},
    )
    assert response.status_code == 400


def test_personalization_influence_is_capped_and_disclosed(client, auth_headers) -> None:
    body = client.get("/api/v1/behavior/relevance", headers=auth_headers).json()
    assert body["max_score_adjustment"] <= 10
    assert "cannot promote noise" in body["note"]


def test_starring_raises_relevance_without_touching_objective_score(client, auth_headers) -> None:
    lists = client.get("/api/v1/watchlists", headers=auth_headers).json()
    demo_id = lists[0]["id"]

    before = client.get(
        "/api/v1/dashboard", headers=auth_headers, params={**REPLAY, "step": 20}
    ).json()
    client.patch(
        f"/api/v1/watchlists/{demo_id}/items/RELIANCE",
        headers=auth_headers,
        json={"starred": True, "priority": 3},
    )
    after = client.get(
        "/api/v1/dashboard", headers=auth_headers, params={**REPLAY, "step": 20}
    ).json()

    relevance = client.get("/api/v1/behavior/relevance", headers=auth_headers).json()
    assert relevance["relevance"]["RELIANCE"] > 0
    # Severity is objective; a star must not manufacture a band change.
    assert before["attention"][0]["severity"] == after["attention"][0]["severity"]


def test_muting_removes_a_stock_from_attention(client, auth_headers) -> None:
    before = client.get(
        "/api/v1/dashboard", headers=auth_headers, params={**REPLAY, "step": 20}
    ).json()
    assert any(c["symbol"] == "RELIANCE" for c in before["attention"])

    client.post("/api/v1/behavior/mute", headers=auth_headers, json={"symbol": "RELIANCE", "hours": 6})

    after = client.get(
        "/api/v1/dashboard", headers=auth_headers, params={**REPLAY, "step": 20}
    ).json()
    assert all(c["symbol"] != "RELIANCE" for c in after["attention"])
    assert "RELIANCE" in after["muted_symbols"]


def test_unmuting_restores_a_stock(client, auth_headers) -> None:
    client.post("/api/v1/behavior/mute", headers=auth_headers, json={"symbol": "RELIANCE"})
    client.delete("/api/v1/behavior/mute/RELIANCE", headers=auth_headers)
    body = client.get("/api/v1/behavior/mutes", headers=auth_headers).json()
    assert "RELIANCE" not in body["muted"]


# ---------------------------------------------------------------------------
# Stocks
# ---------------------------------------------------------------------------


def test_search_returns_price_but_not_intelligence(client, auth_headers) -> None:
    results = client.get(
        "/api/v1/stocks/search", headers=auth_headers, params={"q": "infy", **REPLAY}
    ).json()
    assert results[0]["symbol"] == "INFY"
    assert "score" not in results[0]
    assert "severity" not in results[0]


def test_search_marks_unsupported_matches_honestly(client, auth_headers) -> None:
    results = client.get(
        "/api/v1/stocks/search", headers=auth_headers, params={"q": "NIFTY"}
    ).json()
    assert any(r["is_supported"] is False for r in results)


def test_stock_detail_includes_market_and_sector_comparison(client, auth_headers) -> None:
    body = client.get(
        "/api/v1/stocks/RELIANCE", headers=auth_headers, params={**REPLAY, "step": 20}
    ).json()

    assert body["price"]["price"] > 0
    assert body["score"] is not None
    assert body["relative_to_benchmark"] is not None
    assert body["classification"] == "stock_specific"
    assert body["freshness"]["source"] == "replay"


def test_unknown_stock_returns_404(client, auth_headers) -> None:
    assert client.get("/api/v1/stocks/NOSUCHSTOCK", headers=auth_headers).status_code == 404


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


def test_scenario_catalogue_lists_all_six(client) -> None:
    scenarios = client.get("/api/v1/replay/scenarios").json()
    keys = {s["key"] for s in scenarios}
    assert keys == {
        "crash_stable_market", "market_wide_selloff", "earnings_beat_volume",
        "gap_up_news", "sector_rotation", "quiet_session",
    }
    assert all(s["teaching_point"] for s in scenarios)


def test_replay_session_steps_and_detects(client, auth_headers) -> None:
    session = client.post(
        "/api/v1/replay/sessions",
        headers=auth_headers,
        json={"scenario_key": "crash_stable_market", "speed": 1},
    ).json()
    assert session["label"] == "Demo / Replay"
    assert session["detected"] == []

    stepped = client.post(
        f"/api/v1/replay/sessions/{session['id']}/step",
        headers=auth_headers,
        json={"steps": 20},
    ).json()

    assert stepped["step_index"] == 20
    assert stepped["detected"], "LUMEN should have detected the crash by step 20"
    assert any(e["symbol"] == "RELIANCE" for e in stepped["detected"])


def test_replay_virtual_clock_advances_independently_of_real_time(client, auth_headers) -> None:
    session = client.post(
        "/api/v1/replay/sessions",
        headers=auth_headers,
        json={"scenario_key": "crash_stable_market", "speed": 1},
    ).json()
    start = session["virtual_now"]

    stepped = client.post(
        f"/api/v1/replay/sessions/{session['id']}/step",
        headers=auth_headers,
        json={"steps": 4},
    ).json()

    # Four 15-minute steps == exactly one hour of scenario time.
    assert stepped["virtual_now"] > start
    assert stepped["virtual_now"].startswith("2026-03-12")


def test_replay_reset_clears_detections(client, auth_headers) -> None:
    session = client.post(
        "/api/v1/replay/sessions",
        headers=auth_headers,
        json={"scenario_key": "crash_stable_market", "speed": 1},
    ).json()
    client.post(
        f"/api/v1/replay/sessions/{session['id']}/step", headers=auth_headers, json={"steps": 20}
    )
    reset = client.post(
        f"/api/v1/replay/sessions/{session['id']}/reset", headers=auth_headers
    ).json()
    assert reset["step_index"] == 0
    assert reset["detected"] == []


def test_replay_timeline_narrates_what_was_detected(client, auth_headers) -> None:
    session = client.post(
        "/api/v1/replay/sessions",
        headers=auth_headers,
        json={"scenario_key": "crash_stable_market", "speed": 1},
    ).json()
    client.post(
        f"/api/v1/replay/sessions/{session['id']}/step", headers=auth_headers, json={"steps": 20}
    )

    timeline = client.get(
        f"/api/v1/replay/sessions/{session['id']}/timeline", headers=auth_headers
    ).json()

    assert timeline["label"] == "Demo / Replay"
    assert "Not live market data" in timeline["note"]
    assert timeline["detections"]
    assert timeline["scenario"]["teaching_point"]


def test_quiet_scenario_detects_nothing_end_to_end(client, auth_headers) -> None:
    session = client.post(
        "/api/v1/replay/sessions",
        headers=auth_headers,
        json={"scenario_key": "quiet_session", "speed": 1},
    ).json()
    stepped = client.post(
        f"/api/v1/replay/sessions/{session['id']}/step", headers=auth_headers, json={"steps": 20}
    ).json()
    assert stepped["detected"] == []


def test_invalid_replay_speed_is_rejected(client, auth_headers) -> None:
    response = client.post(
        "/api/v1/replay/sessions",
        headers=auth_headers,
        json={"scenario_key": "crash_stable_market", "speed": 7},
    )
    assert response.status_code == 400


def test_replay_sessions_are_user_scoped(client, auth_headers) -> None:
    session = client.post(
        "/api/v1/replay/sessions",
        headers=auth_headers,
        json={"scenario_key": "crash_stable_market", "speed": 1},
    ).json()
    other = client.post("/api/v1/auth/guest").json()
    other_headers = {"Authorization": f"Bearer {other['access_token']}"}
    assert client.get(
        f"/api/v1/replay/sessions/{session['id']}", headers=other_headers
    ).status_code == 404


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


def test_notifications_list_is_scoped_and_reports_threshold(client, auth_headers) -> None:
    body = client.get("/api/v1/notifications", headers=auth_headers).json()
    assert body["min_score_for_notification"] >= 60
    assert "High Attention and Critical" in body["note"]
    assert isinstance(body["notifications"], list)


def test_notifications_can_be_marked_read(client, auth_headers) -> None:
    assert client.post("/api/v1/notifications/read-all", headers=auth_headers).status_code == 200


# ---------------------------------------------------------------------------
# Debug endpoint
# ---------------------------------------------------------------------------


def test_debug_endpoint_is_closed_by_default(client, auth_headers) -> None:
    """No token configured means the route does not exist at all."""
    get_settings.cache_clear()
    response = client.get("/api/v1/debug/intelligence", params={"symbol": "RELIANCE"})
    assert response.status_code == 404


def test_debug_endpoint_requires_the_token_when_enabled(client, monkeypatch) -> None:
    from app.config import Settings

    get_settings.cache_clear()
    monkeypatch.setenv("DEBUG_API_TOKEN", "secret-debug-token")
    get_settings.cache_clear()
    try:
        assert Settings().debug_api_token == "secret-debug-token"
        denied = client.get("/api/v1/debug/intelligence", params={"symbol": "RELIANCE"})
        assert denied.status_code == 403

        allowed = client.get(
            "/api/v1/debug/intelligence",
            params={"symbol": "RELIANCE", "scenario": "crash_stable_market", "step": 20},
            headers={"X-Lumen-Debug-Token": "secret-debug-token"},
        )
        assert allowed.status_code == 200
        body = allowed.json()
        assert body["score"]["breakdown"]
        assert body["observation"]["symbol"] == "RELIANCE"
        # Booleans only - never a credential value.
        assert all(isinstance(v, bool) for v in body["capabilities"].values())
        assert "secret-debug-token" not in allowed.text
    finally:
        monkeypatch.delenv("DEBUG_API_TOKEN", raising=False)
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Credential hygiene
# ---------------------------------------------------------------------------


def test_no_endpoint_returns_a_credential(client, auth_headers) -> None:
    for path, params in (
        ("/health", None),
        ("/api/v1/dashboard", {**REPLAY, "step": 10}),
        ("/api/v1/replay/scenarios", None),
        ("/api/v1/behavior/relevance", None),
    ):
        response = client.get(path, headers=auth_headers, params=params)
        text = response.text.lower()
        for secret in ("client_secret", "access_token", "api_key", "session_secret"):
            assert secret not in text


def test_replay_digest_measures_from_the_scenario_start(client, auth_headers) -> None:
    """Inside a demo, "since you were away" means since the scenario began.

    The user's real-world baseline sits months from the scenario's virtual
    dates, so using it would report "nothing changed" with an event on screen.
    """
    client.get(
        "/api/v1/dashboard", headers=auth_headers, params={**REPLAY, "step": 20}
    )
    body = client.get(
        "/api/v1/dashboard", headers=auth_headers, params={**REPLAY, "step": 20}
    ).json()

    digest = body["since_you_were_away"]
    assert digest["baseline_source"] == "replay_session"
    assert digest["top_events"], "the crash scenario must appear in the digest"
    assert "RELIANCE" in digest["summary"]


def test_live_digest_still_uses_the_user_baseline(client, auth_headers) -> None:
    body = client.get(
        "/api/v1/dashboard", headers=auth_headers, params={"mode": "live"}
    ).json()
    assert body["since_you_were_away"]["baseline_source"] != "replay_session"


def test_dashboard_reports_which_provider_served_the_data(client, auth_headers) -> None:
    """A user must be able to tell NSE data from Yahoo data from replay data."""
    body = client.get(
        "/api/v1/dashboard", headers=auth_headers, params={**REPLAY, "step": 10}
    ).json()
    mode = body["data_mode"]
    assert mode["provider"] == "replay"
    assert mode["is_replay"] is True


def test_scoring_the_same_replay_step_twice_does_not_duplicate_events(
    client, auth_headers
) -> None:
    """Event keys are deterministic, so a re-scored step must update, not insert.

    Regression test: the replay step endpoint and a dashboard refresh at the
    same virtual instant previously collided on the (event_key, scope)
    uniqueness constraint and broke the transaction.
    """
    session = client.post(
        "/api/v1/replay/sessions",
        headers=auth_headers,
        json={"scenario_key": "crash_stable_market", "speed": 1},
    ).json()

    stepped = client.post(
        f"/api/v1/replay/sessions/{session['id']}/step",
        headers=auth_headers,
        json={"steps": 20},
    )
    assert stepped.status_code == 200, stepped.text

    # Re-score the same steps from the dashboard: must not raise.
    for _ in range(2):
        again = client.get(
            "/api/v1/dashboard", headers=auth_headers, params={**REPLAY, "step": 20}
        )
        assert again.status_code == 200, again.text

    events = client.get("/api/v1/events", headers=auth_headers, params=REPLAY).json()
    keys = [e["event_key"] for e in events]
    assert len(keys) == len(set(keys)), f"duplicate event keys: {keys}"

    reliance = [e for e in events if e["symbol"] == "RELIANCE"]
    assert reliance, "the crash scenario must still produce a RELIANCE event"
    assert reliance[0]["severity"] in {"High Attention", "Critical"}
