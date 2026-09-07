"""Protected intelligence-inspection endpoint.

Exposes the internals of a scoring decision: the normalized observation, every
signal value, family contributions, the objective score, the personalization
adjustment, the final score, confidence and the evidence requirements.

Two safety properties:

* **Closed by default.** With no ``DEBUG_API_TOKEN`` configured the route
  returns 404. An internals endpoint that is public unless disabled is the
  wrong default.
* **No credentials, ever.** Configuration is reported as booleans only, and no
  raw provider payload is included.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.config import Settings, get_settings
from app.auth.dependencies import require_debug_access
from app.intelligence.config import DEFAULT_CONFIG
from app.providers.registry import DataMode, select_provider
from app.replay.scenarios import DEFAULT_SCENARIO_KEY
from app.services import dashboard as dashboard_service
from app.services.explanation import score_rationale
from app.services.pipeline import run_cycle

router = APIRouter(
    prefix="/debug", tags=["debug"], dependencies=[Depends(require_debug_access)]
)


@router.get("/intelligence")
def inspect_intelligence(
    symbol: str = Query(min_length=1, max_length=32),
    mode: DataMode = Query(default=DataMode.REPLAY),
    scenario: str = Query(default=DEFAULT_SCENARIO_KEY),
    step: int = Query(default=12, ge=0, le=25),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Full scoring trace for one instrument at one point in time."""
    symbol = symbol.upper()
    baselines = dashboard_service.baselines_for([symbol])
    if not baselines:
        raise HTTPException(status_code=404, detail=f"No baselines for '{symbol}'.")

    selection = select_provider(mode, scenario, step)
    cycle = run_cycle(selection.provider, baselines)
    scored = cycle.by_symbol().get(symbol)
    if scored is None:
        raise HTTPException(status_code=503, detail=f"No data for '{symbol}'.")

    return {
        "symbol": symbol,
        "data_mode": selection.to_dict(),
        "observation": scored.observation.model_dump(mode="json"),
        "score": score_rationale(scored.result),
        "classification": scored.classification,
        "freshness": scored.freshness.to_dict(),
        "market_context": cycle.market.to_dict(),
        "engine_config": {
            "weights": DEFAULT_CONFIG.weights.model_dump(),
            "meaningful_min_score": DEFAULT_CONFIG.meaningful_min_score,
            "meaningful_min_independent_signals": (
                DEFAULT_CONFIG.meaningful_min_independent_signals
            ),
            "personalization_max_adjustment": (
                DEFAULT_CONFIG.personalization_max_adjustment
            ),
        },
        # Booleans only. No credential value is ever returned here.
        "capabilities": settings.capability_report(),
        "errors": cycle.errors,
    }
