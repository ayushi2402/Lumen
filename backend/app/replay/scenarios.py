"""Replay scenario library.

Each scenario exists to demonstrate one specific thing about LUMEN, and
together they make the central point: **"something moved" and "something
deserves attention" are different claims.**

Scenarios are declarative parameter sets, not recorded tick files. A scenario
says "RELIANCE falls 4.6% with a shock at 11:15 on 3.2x volume while the index
is flat"; the engine in ``replay/engine.py`` turns that into a deterministic
price and volume path. This keeps scenarios readable and tunable, and keeps
bulk tick data out of both the repository and PostgreSQL.

Determinism: every value is a pure function of (scenario key, symbol, step).
No clock, no RNG state carried between calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

STEP_MINUTES = 15
# 09:15 to 15:30 inclusive in 15-minute steps.
TOTAL_STEPS = 25


@dataclass(frozen=True)
class ScenarioNews:
    """A predefined news event, released at a specific step of the replay."""

    symbol: str
    headline: str
    step: int
    relevance: float
    confidence: float
    source: str = "Demo newswire"
    # Almost always False. Set only where the scenario explicitly models a
    # company confirming the cause, so the explainer may use causal language.
    causal_link_established: bool = False


@dataclass(frozen=True)
class SymbolScript:
    """How one instrument behaves over a scenario.

    ``shock_step`` concentrates the move around a moment rather than spreading
    it evenly, which is what makes an event look like an event.
    """

    total_return: float          # percent, close vs previous close
    volume_multiplier: float = 1.0
    gap_percent: float = 0.0
    shock_step: int | None = None


@dataclass(frozen=True)
class Scenario:
    key: str
    name: str
    description: str
    teaching_point: str
    session_date: date

    market_return: float = 0.0                       # NIFTY total move, percent
    sector_returns: dict[str, float] = field(default_factory=dict)
    symbols: dict[str, SymbolScript] = field(default_factory=dict)
    news: tuple[ScenarioNews, ...] = ()

    # Baseline drift applied to every instrument not otherwise scripted.
    ambient_return: float = 0.0
    ambient_volume_multiplier: float = 1.0

    @property
    def total_steps(self) -> int:
        return TOTAL_STEPS

    def news_for(self, symbol: str, step: int) -> ScenarioNews | None:
        """The most recent already-released news item for a symbol."""
        released = [n for n in self.news if n.symbol == symbol and n.step <= step]
        return max(released, key=lambda n: n.step) if released else None


_SESSION = date(2026, 3, 12)


# ---------------------------------------------------------------------------
# 1. Stock crashes while the market is stable
# ---------------------------------------------------------------------------
CRASH_STABLE_MARKET = Scenario(
    key="crash_stable_market",
    name="Sharp decline in a calm market",
    description=(
        "RELIANCE falls sharply on heavy volume while the index barely moves."
    ),
    teaching_point=(
        "Stock-specific. Because the index is flat, the entire move is excess "
        "return - LUMEN scores this high and says the move belongs to the stock."
    ),
    session_date=_SESSION,
    market_return=-0.18,
    sector_returns={"Energy": -0.9},
    symbols={
        "RELIANCE": SymbolScript(
            total_return=-4.6, volume_multiplier=3.2, gap_percent=-1.1, shock_step=8
        ),
    },
    news=(
        ScenarioNews(
            symbol="RELIANCE",
            headline="Regulator opens review into refining margins",
            step=7,
            relevance=0.88,
            confidence=0.82,
        ),
    ),
)


# ---------------------------------------------------------------------------
# 2. Stock falls because the whole market falls
# ---------------------------------------------------------------------------
MARKET_WIDE_SELLOFF = Scenario(
    key="market_wide_selloff",
    name="Broad market selloff",
    description="Nearly everything falls together after a global risk-off move.",
    teaching_point=(
        "The single most important scenario. HDFCBANK drops ~4%, which any "
        "percent-change list would headline. LUMEN does not: the index fell "
        "just as far, so there is almost no excess return and nothing "
        "stock-specific to explain. It is reported as market-wide context, "
        "not as an event demanding attention."
    ),
    session_date=_SESSION,
    market_return=-3.9,
    sector_returns={
        "Banking": -4.1, "IT": -3.6, "Energy": -3.8, "Automobile": -4.0,
        "Metals": -4.4, "Pharma": -3.2, "FMCG": -2.8, "Financial Services": -4.2,
    },
    symbols={
        "HDFCBANK": SymbolScript(total_return=-4.05, volume_multiplier=1.25, gap_percent=-1.4),
        "ICICIBANK": SymbolScript(total_return=-4.2, volume_multiplier=1.3, gap_percent=-1.5),
        "INFY": SymbolScript(total_return=-3.55, volume_multiplier=1.15, gap_percent=-1.2),
    },
    ambient_return=-3.7,
    ambient_volume_multiplier=1.2,
)


# ---------------------------------------------------------------------------
# 3. Positive earnings with abnormal volume
# ---------------------------------------------------------------------------
EARNINGS_BEAT_VOLUME = Scenario(
    key="earnings_beat_volume",
    name="Earnings beat on heavy volume",
    description=(
        "TATAMOTORS rises modestly, but on more than four times normal volume "
        "immediately after results."
    ),
    teaching_point=(
        "A small price move that still matters. At +2.2% this would sit far "
        "down a percent-change list, but 4.6x volume plus a high-relevance "
        "news event is strong corroboration, and LUMEN surfaces it."
    ),
    session_date=_SESSION,
    market_return=0.22,
    sector_returns={"Automobile": 0.6},
    symbols={
        "TATAMOTORS": SymbolScript(
            total_return=2.2, volume_multiplier=4.6, gap_percent=0.5, shock_step=5
        ),
    },
    news=(
        ScenarioNews(
            symbol="TATAMOTORS",
            headline="Q4 results beat estimates; margin guidance raised",
            step=4,
            relevance=0.94,
            confidence=0.9,
        ),
    ),
)


# ---------------------------------------------------------------------------
# 4. Gap up on news
# ---------------------------------------------------------------------------
GAP_UP_NEWS = Scenario(
    key="gap_up_news",
    name="Gap up on overnight news",
    description="TATASTEEL opens 3.1% higher after an overnight announcement.",
    teaching_point=(
        "The move happened before the session opened. The gap signal - judged "
        "against this stock's own historical gap distribution, not a fixed "
        "threshold - carries the event, and the timeline starts at the open."
    ),
    session_date=_SESSION,
    market_return=0.35,
    sector_returns={"Metals": 1.6},
    symbols={
        "TATASTEEL": SymbolScript(
            total_return=3.9, volume_multiplier=2.9, gap_percent=3.1
        ),
    },
    news=(
        ScenarioNews(
            symbol="TATASTEEL",
            headline="Long-term supply agreement signed with European buyer",
            step=0,
            relevance=0.86,
            confidence=0.8,
        ),
    ),
)


# ---------------------------------------------------------------------------
# 5. Sector-wide movement
# ---------------------------------------------------------------------------
SECTOR_ROTATION = Scenario(
    key="sector_rotation",
    name="IT sector selloff",
    description="The whole IT sector falls while the broader index holds up.",
    teaching_point=(
        "Between the two extremes. INFY falls well below the index, so it is "
        "not market-wide - but its sector peers all fell the same amount, so "
        "the sector-relative signal stays quiet. LUMEN reports a sector story "
        "rather than a company-specific one."
    ),
    session_date=_SESSION,
    market_return=-0.45,
    sector_returns={"IT": -3.4},
    symbols={
        "INFY": SymbolScript(total_return=-3.5, volume_multiplier=1.9, gap_percent=-1.0),
        "TCS": SymbolScript(total_return=-3.2, volume_multiplier=1.7, gap_percent=-0.9),
        "WIPRO": SymbolScript(total_return=-3.6, volume_multiplier=1.8, gap_percent=-1.1),
        "HCLTECH": SymbolScript(total_return=-3.3, volume_multiplier=1.6, gap_percent=-0.8),
        "TECHM": SymbolScript(total_return=-3.45, volume_multiplier=1.75),
        "LTIM": SymbolScript(total_return=-3.55, volume_multiplier=1.65),
    },
    news=(
        ScenarioNews(
            symbol="INFY",
            headline="US client spending survey points to slower IT budgets",
            step=3,
            relevance=0.6,
            confidence=0.55,
        ),
    ),
)


# ---------------------------------------------------------------------------
# 6. A quiet session
# ---------------------------------------------------------------------------
QUIET_SESSION = Scenario(
    key="quiet_session",
    name="Quiet session - nothing meaningful",
    description="Ordinary drift, ordinary volume, no news.",
    teaching_point=(
        "Proof that the threshold is real. Prices move all day and LUMEN "
        "reports no events at all. A product that always finds something to "
        "show is not filtering anything."
    ),
    session_date=_SESSION,
    market_return=0.12,
    symbols={
        "RELIANCE": SymbolScript(total_return=0.35, volume_multiplier=0.95),
        "INFY": SymbolScript(total_return=-0.42, volume_multiplier=1.05),
        "HDFCBANK": SymbolScript(total_return=0.28, volume_multiplier=0.9),
        "TATAMOTORS": SymbolScript(total_return=-0.55, volume_multiplier=1.1),
    },
    ambient_return=0.1,
)


SCENARIOS: dict[str, Scenario] = {
    s.key: s
    for s in (
        CRASH_STABLE_MARKET,
        MARKET_WIDE_SELLOFF,
        EARNINGS_BEAT_VOLUME,
        GAP_UP_NEWS,
        SECTOR_ROTATION,
        QUIET_SESSION,
    )
}

DEFAULT_SCENARIO_KEY = CRASH_STABLE_MARKET.key


def get_scenario(key: str) -> Scenario:
    if key not in SCENARIOS:
        raise KeyError(f"Unknown scenario '{key}'. Available: {sorted(SCENARIOS)}")
    return SCENARIOS[key]


def list_scenarios() -> list[dict]:
    """Scenario catalogue for the demo picker."""
    return [
        {
            "key": s.key,
            "name": s.name,
            "description": s.description,
            "teaching_point": s.teaching_point,
            "focus_symbols": sorted(s.symbols),
            "total_steps": s.total_steps,
            "step_minutes": STEP_MINUTES,
        }
        for s in SCENARIOS.values()
    ]
