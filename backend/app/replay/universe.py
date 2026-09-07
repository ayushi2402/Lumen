"""The 50-instrument replay universe.

HONESTY NOTE
------------
These are real NSE symbols, sectors and roughly realistic price levels, but
the statistical baselines (typical daily move, volatility, average volume,
gap dispersion) are **derived deterministically from a fixed seed**, not
downloaded from an exchange. This machine has no Upstox credentials and no
market-data access, so no real historical candles could be fetched.

That is a data-sourcing gap, not an architectural one. ``build_universe()``
returns exactly the baseline shape that
``services.baselines.compute_from_candles`` produces from real Upstox
historical candles, so replacing synthetic baselines with real ones is a
swap of this one function, and nothing downstream changes.

Nothing here is ever presented to a user as live market data. Every response
built from it is tagged ``is_replay=True`` and labelled "Demo / Replay".
"""

from __future__ import annotations

import random
from dataclasses import dataclass

# symbol, company name, sector, reference previous close (INR)
_REFERENCE: tuple[tuple[str, str, str, float], ...] = (
    # --- Information technology ---
    ("TCS", "Tata Consultancy Services", "IT", 3_820.0),
    ("INFY", "Infosys", "IT", 1_500.0),
    ("WIPRO", "Wipro", "IT", 260.0),
    ("HCLTECH", "HCL Technologies", "IT", 1_640.0),
    ("TECHM", "Tech Mahindra", "IT", 1_480.0),
    ("LTIM", "LTIMindtree", "IT", 5_300.0),
    # --- Banking ---
    ("HDFCBANK", "HDFC Bank", "Banking", 1_680.0),
    ("ICICIBANK", "ICICI Bank", "Banking", 1_240.0),
    ("SBIN", "State Bank of India", "Banking", 820.0),
    ("KOTAKBANK", "Kotak Mahindra Bank", "Banking", 1_760.0),
    ("AXISBANK", "Axis Bank", "Banking", 1_120.0),
    ("INDUSINDBK", "IndusInd Bank", "Banking", 980.0),
    # --- Financial services ---
    ("BAJFINANCE", "Bajaj Finance", "Financial Services", 6_900.0),
    ("BAJAJFINSV", "Bajaj Finserv", "Financial Services", 1_640.0),
    ("SBILIFE", "SBI Life Insurance", "Financial Services", 1_480.0),
    ("HDFCLIFE", "HDFC Life Insurance", "Financial Services", 640.0),
    # --- Energy and utilities ---
    ("RELIANCE", "Reliance Industries", "Energy", 1_300.0),
    ("ONGC", "Oil & Natural Gas Corporation", "Energy", 245.0),
    ("IOC", "Indian Oil Corporation", "Energy", 138.0),
    ("BPCL", "Bharat Petroleum", "Energy", 305.0),
    ("GAIL", "GAIL India", "Energy", 190.0),
    ("NTPC", "NTPC", "Utilities", 355.0),
    ("POWERGRID", "Power Grid Corporation", "Utilities", 320.0),
    # --- Automobiles ---
    ("MARUTI", "Maruti Suzuki India", "Automobile", 11_800.0),
    ("TATAMOTORS", "Tata Motors", "Automobile", 1_000.0),
    ("M&M", "Mahindra & Mahindra", "Automobile", 2_880.0),
    ("BAJAJ-AUTO", "Bajaj Auto", "Automobile", 9_100.0),
    ("EICHERMOT", "Eicher Motors", "Automobile", 4_850.0),
    ("HEROMOTOCO", "Hero MotoCorp", "Automobile", 4_400.0),
    # --- Metals and mining ---
    ("TATASTEEL", "Tata Steel", "Metals", 142.0),
    ("JSWSTEEL", "JSW Steel", "Metals", 960.0),
    ("HINDALCO", "Hindalco Industries", "Metals", 655.0),
    ("VEDL", "Vedanta", "Metals", 445.0),
    ("COALINDIA", "Coal India", "Metals", 395.0),
    # --- Pharmaceuticals and healthcare ---
    ("SUNPHARMA", "Sun Pharmaceutical", "Pharma", 1_760.0),
    ("DRREDDY", "Dr. Reddy's Laboratories", "Pharma", 1_240.0),
    ("CIPLA", "Cipla", "Pharma", 1_520.0),
    ("DIVISLAB", "Divi's Laboratories", "Pharma", 5_900.0),
    ("APOLLOHOSP", "Apollo Hospitals", "Healthcare", 7_100.0),
    # --- FMCG ---
    ("HINDUNILVR", "Hindustan Unilever", "FMCG", 2_420.0),
    ("ITC", "ITC", "FMCG", 425.0),
    ("NESTLEIND", "Nestle India", "FMCG", 2_240.0),
    ("BRITANNIA", "Britannia Industries", "FMCG", 4_820.0),
    ("TATACONSUM", "Tata Consumer Products", "FMCG", 945.0),
    # --- Infrastructure and materials ---
    ("LT", "Larsen & Toubro", "Infrastructure", 3_640.0),
    ("ULTRACEMCO", "UltraTech Cement", "Cement", 11_400.0),
    ("GRASIM", "Grasim Industries", "Cement", 2_620.0),
    ("ADANIPORTS", "Adani Ports & SEZ", "Infrastructure", 1_340.0),
    # --- Telecom and consumer ---
    ("BHARTIARTL", "Bharti Airtel", "Telecom", 1_620.0),
    ("TITAN", "Titan Company", "Consumer", 3_380.0),
)

BENCHMARK_SYMBOL = "NIFTY50"
BENCHMARK_NAME = "NIFTY 50"

# Sector-typical volatility in percent per day. Used as the centre of each
# instrument's synthetic volatility, so a utility is calmer than a metals name
# - which is the property that makes volatility-normalization meaningful.
_SECTOR_VOLATILITY = {
    "IT": 1.35,
    "Banking": 1.25,
    "Financial Services": 1.55,
    "Energy": 1.45,
    "Utilities": 1.05,
    "Automobile": 1.60,
    "Metals": 2.10,
    "Pharma": 1.40,
    "Healthcare": 1.50,
    "FMCG": 0.95,
    "Infrastructure": 1.55,
    "Cement": 1.35,
    "Telecom": 1.30,
    "Consumer": 1.45,
}

_UNIVERSE_SEED = 20260312


@dataclass(frozen=True)
class InstrumentBaseline:
    """Reference data plus the statistical baselines the engine requires.

    This is the exact shape ``services.baselines`` produces from real
    historical candles.
    """

    symbol: str
    name: str
    sector: str
    previous_close: float

    typical_daily_move: float
    typical_move_sample_days: int
    historical_volatility: float
    volatility_sample_days: int
    average_volume: float
    historical_gap_stdev: float

    @property
    def provider_key(self) -> str:
        """Upstox-style instrument key. Synthetic for replay instruments."""
        return f"NSE_EQ|{self.symbol}"

    def same_time_volume(self, minute_of_session: int, session_minutes: int) -> float:
        """Expected cumulative volume by this point of the session.

        Uses a U-shaped intraday profile: heavy at the open, thin midday,
        heavy into the close. A flat profile would make every midday reading
        look abnormally quiet and every closing reading look abnormally busy.
        """
        if session_minutes <= 0:
            return self.average_volume
        fraction = min(1.0, max(0.0, minute_of_session / session_minutes))
        # Cumulative share of the day's volume traded by ``fraction``.
        shape = 0.5 * (fraction ** 0.65) + 0.5 * (fraction ** 1.8)
        return max(1.0, self.average_volume * shape)


def build_universe(seed: int = _UNIVERSE_SEED) -> dict[str, InstrumentBaseline]:
    """Build the 50-instrument universe deterministically.

    The same seed always yields the same baselines, so replay runs, tests and
    demos are byte-identical.
    """
    rng = random.Random(seed)
    universe: dict[str, InstrumentBaseline] = {}

    for symbol, name, sector, close in _REFERENCE:
        sector_vol = _SECTOR_VOLATILITY.get(sector, 1.4)
        volatility = round(sector_vol * rng.uniform(0.85, 1.15), 3)
        # Mean absolute daily move runs a little below one standard deviation.
        typical_move = round(volatility * rng.uniform(0.68, 0.82), 3)
        # Turnover scales inversely with price; large-caps trade in size.
        notional = rng.uniform(2.2e9, 9.5e9)
        average_volume = round(max(2.0e5, notional / close), 0)
        gap_stdev = round(volatility * rng.uniform(0.38, 0.58), 3)

        universe[symbol] = InstrumentBaseline(
            symbol=symbol,
            name=name,
            sector=sector,
            previous_close=close,
            typical_daily_move=typical_move,
            typical_move_sample_days=30,
            historical_volatility=volatility,
            volatility_sample_days=30,
            average_volume=average_volume,
            historical_gap_stdev=gap_stdev,
        )

    return universe


UNIVERSE: dict[str, InstrumentBaseline] = build_universe()
SYMBOLS: tuple[str, ...] = tuple(UNIVERSE)

SECTORS: dict[str, list[str]] = {}
for _baseline in UNIVERSE.values():
    SECTORS.setdefault(_baseline.sector, []).append(_baseline.symbol)


# The curated demo watchlist. Smaller than the replay universe on purpose:
# the universe supplies market breadth and sector context, while the user sees
# a readable list. Chosen to span sectors so market-wide vs stock-specific
# movement is visible.
DEMO_WATCHLIST: tuple[str, ...] = (
    "RELIANCE",
    "TCS",
    "INFY",
    "HDFCBANK",
    "ICICIBANK",
    "SBIN",
    "TATAMOTORS",
    "MARUTI",
    "TATASTEEL",
    "SUNPHARMA",
    "ITC",
    "LT",
    "BHARTIARTL",
)


def sector_of(symbol: str) -> str | None:
    baseline = UNIVERSE.get(symbol)
    return baseline.sector if baseline else None


def sector_peers(symbol: str) -> list[str]:
    """Other instruments in the same sector, for sector-relative context."""
    sector = sector_of(symbol)
    if sector is None:
        return []
    return [s for s in SECTORS.get(sector, []) if s != symbol]
