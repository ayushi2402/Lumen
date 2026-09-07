"""Replay / demo routes.

Every response from this router is labelled "Demo / Replay". Replay data is
never returned from a live-mode endpoint, and the label is part of the payload
rather than something the frontend is trusted to remember.

The virtual clock lives in the persisted ``ReplaySession`` row, so stepping is
driven by explicit user action and is unaffected by wall-clock time - the demo
behaves identically at any hour, on any day.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import current_user, get_db_session
from app.db.models import ReplaySession, User, utcnow
from app.providers.replay import ReplayProvider
from app.replay.scenarios import STEP_MINUTES, get_scenario, list_scenarios
from app.replay.universe import DEMO_WATCHLIST
from app.schemas.api import ReplayControlIn, ReplaySessionOut, ReplayStartIn, ScenarioOut
from app.services import catalog, dashboard as dashboard_service
from app.services import events as events_service
from app.services import serializers
from app.services import watchlists as watchlists_service
from app.services.pipeline import run_cycle

router = APIRouter(prefix="/replay", tags=["replay"])

REPLAY_LABEL = "Demo / Replay"
ALLOWED_SPEEDS = (1, 10, 60)


@router.get("/scenarios", response_model=list[ScenarioOut])
def scenarios() -> list[dict]:
    """The scenario library shown in the demo picker."""
    return list_scenarios()


def _scope(scenario_key: str) -> str:
    return f"replay:{scenario_key}"


def _session_payload(
    db: Session, user: User, session: ReplaySession, detected: list, market: dict | None
) -> dict:
    scenario = get_scenario(session.scenario_key)
    states = events_service.states_for(db, user, [r.id for r in detected])
    return {
        "id": session.id,
        "scenario_key": session.scenario_key,
        "scenario_name": scenario.name,
        "speed": session.speed,
        "status": session.status,
        "step_index": session.step_index,
        "total_steps": scenario.total_steps,
        "virtual_now": session.virtual_now,
        "label": REPLAY_LABEL,
        "detected": [serializers.event_summary(r, states.get(r.id)) for r in detected],
        "market": market,
    }


def _get_session(db: Session, user: User, session_id: int) -> ReplaySession:
    session = db.scalar(
        select(ReplaySession).where(
            ReplaySession.id == session_id, ReplaySession.user_id == user.id
        )
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Replay session not found.")
    return session


def _advance(
    db: Session, user: User, session: ReplaySession, steps: int
) -> tuple[list, dict]:
    """Move the virtual clock and run the engine at the new step.

    Every intermediate step is scored, not just the destination, so events
    evolve exactly as they would in a live session rather than jumping.
    """
    scenario = get_scenario(session.scenario_key)
    scope = _scope(session.scenario_key)
    symbols = watchlists_service.watched_symbols(db, user) or list(DEMO_WATCHLIST)
    baselines = dashboard_service.baselines_for(symbols, db)
    instruments = catalog.get_many(db, list(baselines))

    market: dict = {}
    target = min(scenario.total_steps, session.step_index + steps)

    for step in range(session.step_index + 1, target + 1):
        provider = ReplayProvider(scenario, step=step)
        cycle = run_cycle(provider, baselines)
        market = cycle.market.to_dict()

        for scored in cycle.scored:
            instrument = instruments.get(scored.symbol)
            if instrument is None:
                continue
            events_service.ingest_scored(
                db, scored, instrument, scope=scope, is_replay=True
            )

        session.step_index = step
        session.virtual_now = provider.virtual_time(step)

    if session.step_index >= scenario.total_steps:
        session.status = "complete"
    else:
        session.status = "running"

    db.flush()
    detected = events_service.events_for_symbols(db, symbols, scope=scope, limit=25)
    detected.sort(key=lambda r: -r.peak_score)
    return detected, market


@router.post("/sessions", response_model=ReplaySessionOut, status_code=status.HTTP_201_CREATED)
def start_session(
    body: ReplayStartIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db_session),
) -> dict:
    """Start a scenario at step 0. Nothing has been detected yet."""
    try:
        scenario = get_scenario(body.scenario_key)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if body.speed not in ALLOWED_SPEEDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Speed must be one of {ALLOWED_SPEEDS} (or use step control).",
        )

    provider = ReplayProvider(scenario, step=0)
    start = provider.virtual_time(0)
    session = ReplaySession(
        user_id=user.id,
        scenario_key=scenario.key,
        speed=body.speed,
        status="ready",
        virtual_start=start,
        virtual_now=start,
        step_index=0,
    )
    db.add(session)
    db.flush()

    # A guest arriving straight from the landing page needs something to watch.
    if not watchlists_service.watched_symbols(db, user):
        watchlists_service.create_demo_watchlist(db, user)

    db.commit()
    return _session_payload(db, user, session, [], None)


@router.post("/sessions/{session_id}/step", response_model=ReplaySessionOut)
def step_session(
    session_id: int,
    body: ReplayControlIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db_session),
) -> dict:
    """Advance the virtual clock by N scenario steps."""
    session = _get_session(db, user, session_id)
    detected, market = _advance(db, user, session, body.steps)
    db.commit()
    return _session_payload(db, user, session, detected, market)


@router.post("/sessions/{session_id}/play", response_model=ReplaySessionOut)
def play_session(
    session_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db_session),
) -> dict:
    """Advance by the session's speed multiplier.

    1x moves one step, 10x moves ten, 60x runs to the end of the session. The
    multiplier scales scenario time, never real time.
    """
    session = _get_session(db, user, session_id)
    scenario = get_scenario(session.scenario_key)
    steps = {1: 1, 10: 10, 60: scenario.total_steps}.get(session.speed, 1)
    detected, market = _advance(db, user, session, steps)
    db.commit()
    return _session_payload(db, user, session, detected, market)


@router.post("/sessions/{session_id}/speed", response_model=ReplaySessionOut)
def set_speed(
    session_id: int,
    speed: int = Query(ge=1, le=60),
    user: User = Depends(current_user),
    db: Session = Depends(get_db_session),
) -> dict:
    if speed not in ALLOWED_SPEEDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Speed must be one of {ALLOWED_SPEEDS}.",
        )
    session = _get_session(db, user, session_id)
    session.speed = speed
    db.commit()
    return _session_payload(db, user, session, [], None)


@router.post("/sessions/{session_id}/reset", response_model=ReplaySessionOut)
def reset_session(
    session_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db_session),
) -> dict:
    """Rewind to step 0 and clear everything this session detected."""
    session = _get_session(db, user, session_id)
    scenario = get_scenario(session.scenario_key)
    scope = _scope(session.scenario_key)

    symbols = watchlists_service.watched_symbols(db, user) or list(DEMO_WATCHLIST)
    for row in events_service.events_for_symbols(db, symbols, scope=scope, limit=500):
        db.delete(row)

    provider = ReplayProvider(scenario, step=0)
    session.step_index = 0
    session.virtual_now = provider.virtual_time(0)
    session.status = "ready"
    db.commit()
    return _session_payload(db, user, session, [], None)


@router.get("/sessions/{session_id}", response_model=ReplaySessionOut)
def get_session(
    session_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db_session),
) -> dict:
    session = _get_session(db, user, session_id)
    scope = _scope(session.scenario_key)
    symbols = watchlists_service.watched_symbols(db, user) or list(DEMO_WATCHLIST)
    detected = events_service.events_for_symbols(db, symbols, scope=scope, limit=25)
    detected.sort(key=lambda r: -r.peak_score)
    return _session_payload(db, user, session, detected, None)


@router.get("/sessions/{session_id}/timeline")
def session_timeline(
    session_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db_session),
) -> dict:
    """What LUMEN detected, and when, during this replay.

    This is the "LUMEN detected..." narrative: an ordered record of detections
    with the virtual timestamps they happened at.
    """
    session = _get_session(db, user, session_id)
    scenario = get_scenario(session.scenario_key)
    scope = _scope(session.scenario_key)
    symbols = watchlists_service.watched_symbols(db, user) or list(DEMO_WATCHLIST)
    rows = events_service.events_for_symbols(db, symbols, scope=scope, limit=100)

    detections = [
        {
            "at": row.started_at.isoformat(),
            "symbol": row.instrument.symbol,
            "headline": row.headline,
            "severity": row.severity,
            "score": round(row.peak_score, 2),
            "classification": serializers.classification_of(row),
            "reason": serializers.primary_reason(row),
        }
        for row in sorted(rows, key=lambda r: r.started_at)
    ]

    return {
        "label": REPLAY_LABEL,
        "scenario": {
            "key": scenario.key,
            "name": scenario.name,
            "description": scenario.description,
            "teaching_point": scenario.teaching_point,
        },
        "virtual_now": session.virtual_now.isoformat(),
        "step_index": session.step_index,
        "total_steps": scenario.total_steps,
        "step_minutes": STEP_MINUTES,
        "detections": detections,
        "detected_count": len(detections),
        "note": "Synthetic scenario data for demonstration. Not live market data.",
        "generated_at": utcnow().isoformat(),
    }
