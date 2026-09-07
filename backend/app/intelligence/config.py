"""Every tunable number in LUMEN's intelligence engine, in one place.

Nothing in ``signals.py``, ``scoring.py`` or ``events.py`` may contain a bare
numeric threshold. They all read from here.

The engine functions take a config argument defaulting to ``DEFAULT_CONFIG``.
The object is frozen, so this is a shared immutable default rather than mutable
global state - functions stay pure and tests can pass a variant config without
affecting anything else.

REFERENCE SCALES
----------------
Most ``*_full_strength`` values answer the question "what magnitude counts as a
maximally strong instance of this signal?". A signal at that magnitude scores
1.0; anything beyond is clamped. Clamped linear ramps are used deliberately
over smooth curves: they are trivial to explain to a user in the
"How LUMEN calculated this" panel.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.intelligence.models import SignalFamily


class ScoringWeights(BaseModel):
    """Maximum points each signal family can contribute. Sums to 100.

    Because the maximum is 100 across six families, no single family can push
    an event into High Attention on its own. That is the structural guarantee
    behind "one signal must never create a high-attention event" - it falls out
    of the weights rather than relying on a special-case rule.
    """

    model_config = ConfigDict(frozen=True)

    price_movement: float = 25.0
    relative_performance: float = 22.0
    volume: float = 20.0
    volatility: float = 12.0
    gap: float = 10.0
    news: float = 11.0

    def as_map(self) -> dict[SignalFamily, float]:
        return {
            SignalFamily.PRICE_MOVEMENT: self.price_movement,
            SignalFamily.RELATIVE_PERFORMANCE: self.relative_performance,
            SignalFamily.VOLUME: self.volume,
            SignalFamily.VOLATILITY: self.volatility,
            SignalFamily.GAP: self.gap,
            SignalFamily.NEWS: self.news,
        }

    @property
    def total(self) -> float:
        return sum(self.as_map().values())


class IntelligenceConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    weights: ScoringWeights = Field(default_factory=ScoringWeights)

    # --- Signal reference scales (see module docstring) ---------------------
    price_move_full_strength: float = Field(
        default=5.0, description="Percent move scoring full price-movement strength."
    )
    historical_ratio_full_strength: float = Field(
        default=3.0, description="Multiples of typical daily move for full strength."
    )
    volatility_sigma_full_strength: float = Field(
        default=3.0, description="Standard deviations of move for full strength."
    )
    relative_pp_full_strength: float = Field(
        default=3.0, description="Percentage points vs benchmark for full strength."
    )
    volume_ratio_full_strength: float = Field(
        default=4.0, description="Multiples of baseline volume for full strength."
    )
    volatility_ratio_full_strength: float = Field(
        default=2.5, description="Intraday/historical vol ratio for full strength."
    )
    gap_percent_full_strength: float = Field(
        default=2.5, description="Percent gap for full strength."
    )
    gap_sigma_full_strength: float = Field(
        default=2.5, description="Gap standard deviations for full strength."
    )

    # --- Minimum history before a signal may be computed --------------------
    min_volatility_sample_days: int = 10
    min_typical_move_sample_days: int = 10
    min_same_time_volume_sample_days: int = 5
    min_intraday_observations: int = 6

    # --- Signal confidence --------------------------------------------------
    confidence_full_history: float = Field(
        default=0.9, description="Signal confidence with a proper baseline."
    )
    confidence_fallback_baseline: float = Field(
        default=0.55,
        description="Signal confidence when a fallback baseline had to be used.",
    )
    news_unknown_timing_penalty: float = Field(
        default=0.8,
        description="Strength multiplier when news timing is unknown.",
    )

    # --- Noise floors: below these, a signal contributes nothing -------------
    min_price_move_percent: float = Field(
        default=0.5, description="Moves smaller than this are not an event at all."
    )
    min_relative_pp: float = 0.4
    min_volume_ratio: float = Field(
        default=1.3, description="Below this multiple, volume is simply normal."
    )
    min_volatility_ratio: float = 1.25
    min_gap_percent: float = 0.5

    # --- Severity bands (lower bound, inclusive) ----------------------------
    severity_worth_watching: float = 40.0
    severity_high_attention: float = 60.0
    severity_critical: float = 80.0

    # --- Meaningfulness gate ------------------------------------------------
    meaningful_min_score: float = Field(
        default=40.0, description="Objective score required to be an event."
    )
    meaningful_min_independent_signals: int = Field(
        default=2,
        description=(
            "Independent signal families required. Enforces that corroboration, "
            "not raw magnitude, is what makes something meaningful."
        ),
    )

    # --- Confidence banding -------------------------------------------------
    confidence_high_min_signals: int = 4
    confidence_high_min_coverage: float = Field(
        default=0.85,
        description=(
            "Set so that a single missing evidence family drops an otherwise "
            "strong reading to medium confidence. A high score with incomplete "
            "evidence is exactly the case that must not be reported as certain."
        ),
    )
    confidence_high_min_avg: float = 0.7
    confidence_low_max_signals: int = 1
    confidence_low_max_coverage: float = 0.4

    # --- Personalization ----------------------------------------------------
    personalization_max_adjustment: float = Field(
        default=8.0,
        description=(
            "Hard cap in points. Small by design: personalization reorders "
            "genuinely significant events, it never manufactures significance."
        ),
    )
    personalization_min_objective_score: float = Field(
        default=40.0,
        description=(
            "Below this objective score no adjustment is applied at all, so "
            "behaviour cannot lift noise toward a higher severity band."
        ),
    )

    # --- Event grouping and evolution ---------------------------------------
    event_continuation_window_minutes: int = Field(
        default=180,
        description="Gap after which a new observation starts a fresh event.",
    )
    event_stale_after_minutes: int = Field(
        default=240, description="Idle time after which an active event goes stale."
    )
    event_material_change_points: float = Field(
        default=20.0,
        description=(
            "Score jump beyond an acknowledged event's peak that justifies "
            "raising a new event rather than silently updating a reviewed one."
        ),
    )


DEFAULT_CONFIG = IntelligenceConfig()
