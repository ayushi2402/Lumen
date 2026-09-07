"""Replay market-data provider.

Turns a declarative ``Scenario`` into deterministic Upstox-shaped raw quote
payloads. It implements the same ``MarketDataProvider`` interface as the live
provider and emits the same payload shape, so the normalization layer, the
engine and the API are all exercised by exactly the code path production uses.

Two properties matter most:

* **Deterministic.** Every price and volume is a pure function of
  (scenario key, symbol, step). No RNG state is carried between calls, so
  stepping forward, seeking, or re-running produces identical results.
* **Independent of system time.** Timestamps come from the scenario's virtual
  session, never from ``datetime.now()``. Replay behaves the same at 3am on a
  Sunday as at 11am on a Tuesday, which is what makes it demo-safe.

Every response is tagged ``is_replay=True``. Nothing produced here is ever
presented as live market data.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from app.market.calendar import IST, SESSION_MINUTES, SESSION_OPEN, to_utc_naive
from app.providers.base import ProviderStatus, RawResponse
from app.replay.scenarios import STEP_MINUTES, Scenario, SymbolScript
from app.replay.universe import UNIVERSE, InstrumentBaseline

PROVIDER_NAME = "replay"


def _hash01(*parts: object) -> float:
    """Deterministic pseudo-random value in [0, 1) from the given parts.

    A hash rather than an RNG so there is no sequence state: the value for
    (scenario, symbol, step) is the same whether it is the first call or the
    thousandth.
    """
    seed = "|".join(str(p) for p in parts)
    digest = 0
    for char in seed:
        digest = (digest * 131 + ord(char)) & 0xFFFFFFFF
    return digest / 0xFFFFFFFF


def _shock_progress(step: int, total_steps: int, shock_step: int | None) -> float:
    """Fraction of the day's move completed by ``step``.

    Without a shock the move accrues smoothly. With one, little happens
    beforehand, most of it lands over about three steps, and it settles after -
    which is what a real news-driven repricing looks like.
    """
    if total_steps <= 0:
        return 1.0
    linear = step / total_steps
    if shock_step is None:
        return linear

    if step < shock_step:
        # Mild drift ahead of the event.
        return 0.18 * (step / max(1, shock_step))
    # Logistic ramp centred just after the shock, asymptoting to 1.
    return 0.18 + 0.82 / (1 + math.exp(-1.6 * (step - shock_step - 1)))


def _volume_ramp(step: int, total_steps: int, shock_step: int | None) -> float:
    """How much of the abnormal volume has arrived by ``step``.

    Volume spikes with the event and stays elevated afterwards, rather than
    being spread evenly across the session.
    """
    if shock_step is None:
        return 1.0
    if step < shock_step:
        return 1.0
    return 1.0 + 0.9 * min(1.0, (step - shock_step + 1) / 3.0)


class ReplayProvider:
    """Serves one scenario at a virtual point in time."""

    name = PROVIDER_NAME
    is_replay = True

    def __init__(self, scenario: Scenario, step: int = 0) -> None:
        self.scenario = scenario
        self.step = step

    # -- clock ------------------------------------------------------------
    def virtual_time(self, step: int | None = None) -> datetime:
        """Virtual instant for a step, as naive UTC."""
        step = self.step if step is None else step
        opened = datetime.combine(self.scenario.session_date, SESSION_OPEN, tzinfo=IST)
        return to_utc_naive(opened + timedelta(minutes=STEP_MINUTES * step))

    def minute_of_session(self, step: int | None = None) -> int:
        step = self.step if step is None else step
        return min(SESSION_MINUTES, STEP_MINUTES * step)

    def seek(self, step: int) -> None:
        self.step = max(0, min(self.scenario.total_steps, step))

    # -- provider interface -----------------------------------------------
    def is_available(self) -> bool:
        """Always available. That is the point of replay."""
        return True

    def _script_for(self, symbol: str) -> SymbolScript:
        scripted = self.scenario.symbols.get(symbol)
        if scripted is not None:
            return scripted
        baseline = UNIVERSE[symbol]
        sector_move = self.scenario.sector_returns.get(baseline.sector)
        total = sector_move if sector_move is not None else self.scenario.ambient_return
        # Idiosyncratic spread around the sector/ambient move so the universe
        # is not artificially uniform - market breadth needs real dispersion.
        jitter = (_hash01(self.scenario.key, symbol, "spread") - 0.5) * baseline.typical_daily_move
        return SymbolScript(
            total_return=total + jitter,
            volume_multiplier=self.scenario.ambient_volume_multiplier,
        )

    def _quote_for(self, symbol: str, step: int) -> dict:
        """Build one raw Upstox-style quote entry."""
        baseline: InstrumentBaseline = UNIVERSE[symbol]
        script = self._script_for(symbol)
        total_steps = self.scenario.total_steps

        progress = _shock_progress(step, total_steps, script.shock_step)
        gap = script.gap_percent
        # The gap is realized instantly at the open; the rest accrues.
        intraday_target = script.total_return - gap
        noise_scale = baseline.typical_daily_move * 0.12
        noise = (_hash01(self.scenario.key, symbol, step, "noise") - 0.5) * 2 * noise_scale

        pct = gap + intraday_target * progress + (noise if step > 0 else 0.0)
        price = baseline.previous_close * (1 + pct / 100.0)
        open_price = baseline.previous_close * (1 + gap / 100.0)

        minute = self.minute_of_session(step)
        expected = baseline.same_time_volume(minute, SESSION_MINUTES)
        volume_noise = 0.85 + 0.3 * _hash01(self.scenario.key, symbol, step, "vol")
        volume = expected * script.volume_multiplier * _volume_ramp(
            step, total_steps, script.shock_step
        ) * volume_noise

        # Intraday high/low bracket the path travelled so far.
        travelled = abs(pct)
        high = max(price, open_price) * (1 + min(0.004, travelled / 400))
        low = min(price, open_price) * (1 - min(0.004, travelled / 400))

        return {
            "instrument_token": baseline.provider_key,
            "last_price": round(price, 2),
            "volume": int(volume),
            "net_change": round(price - baseline.previous_close, 2),
            "ohlc": {
                "open": round(open_price, 2),
                "high": round(high, 2),
                "low": round(low, 2),
                "close": round(baseline.previous_close, 2),
            },
            "last_trade_time": self.virtual_time(step).isoformat(),
        }

    def get_quotes(self, instrument_keys: list[str]) -> RawResponse:
        """Raw quote batch, shaped like an Upstox v2 quote response."""
        step = self.step
        data: dict[str, dict] = {}
        for key in instrument_keys:
            symbol = key.split("|")[-1]
            if symbol not in UNIVERSE:
                continue
            data[f"NSE_EQ:{symbol}"] = self._quote_for(symbol, step)

        moment = self.virtual_time(step)
        return RawResponse(
            provider=self.name,
            payload={"status": "success", "data": data},
            fetched_at=moment,
            as_of=moment,
            status=ProviderStatus.OK,
            is_replay=True,
            meta={
                "scenario": self.scenario.key,
                "step": step,
                "total_steps": self.scenario.total_steps,
                "mode": "replay",
            },
        )

    def get_historical_candles(
        self, instrument_key: str, interval: str, start: datetime, end: datetime
    ) -> RawResponse:
        """Synthetic daily candles.

        Baselines for replay come from ``replay.universe`` directly, so this
        exists to satisfy the interface and to let baseline-building code be
        exercised end to end.
        """
        symbol = instrument_key.split("|")[-1]
        baseline = UNIVERSE[symbol]
        candles: list[list] = []
        cursor = start
        index = 0
        while cursor <= end:
            drift = (_hash01(symbol, index, "hist") - 0.5) * 2 * baseline.historical_volatility
            close = baseline.previous_close * (1 + drift / 100.0)
            candles.append(
                [
                    cursor.isoformat(),
                    round(close * 0.999, 2),
                    round(close * 1.004, 2),
                    round(close * 0.996, 2),
                    round(close, 2),
                    int(baseline.average_volume * (0.8 + 0.4 * _hash01(symbol, index, "hv"))),
                    0,
                ]
            )
            cursor += timedelta(days=1)
            index += 1

        moment = self.virtual_time()
        return RawResponse(
            provider=self.name,
            payload={"status": "success", "data": {"candles": candles}},
            fetched_at=moment,
            as_of=moment,
            is_replay=True,
            meta={"interval": interval, "scenario": self.scenario.key},
        )

    def get_intraday_candles(self, instrument_key: str, interval: str) -> RawResponse:
        """Candles from the open up to the current virtual step.

        Real intraday candles, not a placeholder: realized intraday volatility
        is computed from these, so the volatility signal is genuinely earned
        during replay rather than asserted.
        """
        symbol = instrument_key.split("|")[-1]
        candles = []
        for step in range(0, self.step + 1):
            quote = self._quote_for(symbol, step)
            candles.append(
                [
                    self.virtual_time(step).isoformat(),
                    quote["ohlc"]["open"],
                    quote["ohlc"]["high"],
                    quote["ohlc"]["low"],
                    quote["last_price"],
                    quote["volume"],
                    0,
                ]
            )

        moment = self.virtual_time()
        return RawResponse(
            provider=self.name,
            payload={"status": "success", "data": {"candles": candles}},
            fetched_at=moment,
            as_of=moment,
            is_replay=True,
            meta={"interval": interval, "scenario": self.scenario.key, "step": self.step},
        )

    # -- scenario extras ---------------------------------------------------
    def news_for(self, symbol: str) -> dict | None:
        """Predefined scenario news released on or before the current step."""
        item = self.scenario.news_for(symbol, self.step)
        if item is None:
            return None
        published = self.virtual_time(item.step)
        return {
            "headline": item.headline,
            "source": item.source,
            "published_at": published,
            "relevance": item.relevance,
            "confidence": item.confidence,
            "causal_link_established": item.causal_link_established,
            "minutes_before_move": max(0, (self.step - item.step) * STEP_MINUTES),
        }

    def benchmark_return(self) -> float:
        """NIFTY move realized by the current step."""
        progress = _shock_progress(self.step, self.scenario.total_steps, None)
        return round(self.scenario.market_return * progress, 4)

    def sector_return(self, sector: str) -> float | None:
        target = self.scenario.sector_returns.get(sector)
        if target is None:
            return None
        progress = _shock_progress(self.step, self.scenario.total_steps, None)
        return round(target * progress, 4)
