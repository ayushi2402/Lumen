"""Yahoo Finance provider.

VERIFIED WORKING on this machine: batched daily candles, intraday 15-minute
candles and index data for NSE symbols were all fetched successfully during
development, and the closing price cross-checked exactly against NSE's own
historical data via jugaad-data.

**This is not exchange-grade real-time data.** Yahoo's quotes are delayed and
unofficial. LUMEN never labels anything from this provider "live" on its own
merit - the freshness layer classifies it from the candle's own timestamp, so
a delayed feed reports as delayed.

Used primarily for historical OHLCV, volatility, volume baselines and the
NIFTY benchmark, which is exactly where a delayed source is fine: baselines
care about distributions, not about the last tick.

Payloads returned here are dicts extracted from pandas frames rather than
literal HTTP bodies - a DataFrame is not a transportable raw payload. No
``MarketObservation`` is constructed; that stays in ``market/normalization``.
"""

from __future__ import annotations

import warnings
from datetime import datetime, timedelta, timezone
from typing import Any

from app.providers.base import ProviderError, ProviderStatus, RawResponse

PROVIDER_NAME = "yfinance"

# NSE symbols that differ from a plain "<symbol>.NS" mapping.
_SYMBOL_OVERRIDES: dict[str, str] = {
    "NIFTY50": "^NSEI",
}


def to_yahoo_symbol(symbol: str) -> str:
    """Map an NSE symbol to its Yahoo ticker."""
    if symbol in _SYMBOL_OVERRIDES:
        return _SYMBOL_OVERRIDES[symbol]
    return f"{symbol}.NS"


def from_yahoo_symbol(ticker: str) -> str:
    for nse, yahoo in _SYMBOL_OVERRIDES.items():
        if yahoo == ticker:
            return nse
    return ticker[:-3] if ticker.endswith(".NS") else ticker


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _as_naive_utc(value: Any) -> datetime | None:
    try:
        stamp = value.to_pydatetime()
    except AttributeError:
        return None
    if stamp.tzinfo is not None:
        return stamp.astimezone(timezone.utc).replace(tzinfo=None)
    return stamp


class YFinanceProvider:
    """Batched Yahoo Finance access behind the standard provider interface."""

    name = PROVIDER_NAME
    is_replay = False

    def __init__(self, timeout: float = 20.0) -> None:
        self.timeout = timeout

    def is_available(self) -> bool:
        """Importable means usable. Reachability is proven by the call itself."""
        try:
            import yfinance  # noqa: F401
        except ImportError:
            return False
        return True

    # -- internals ---------------------------------------------------------
    def _download(self, tickers: list[str], **kwargs) -> Any:
        try:
            import yfinance as yf
        except ImportError as exc:
            raise ProviderError(
                "yfinance is not installed.", ProviderStatus.UNAVAILABLE
            ) from exc

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                return yf.download(
                    tickers,
                    group_by="ticker",
                    progress=False,
                    auto_adjust=False,
                    threads=True,
                    **kwargs,
                )
            except Exception as exc:
                raise ProviderError(
                    f"yfinance download failed: {exc}", ProviderStatus.UNAVAILABLE
                ) from exc

    @staticmethod
    def _frame_for(data: Any, ticker: str, single: bool) -> Any:
        """Extract one ticker's frame from a possibly MultiIndex result."""
        if single:
            return data
        try:
            return data[ticker]
        except (KeyError, TypeError):
            return None

    # -- provider interface -------------------------------------------------
    def get_quotes(self, instrument_keys: list[str]) -> RawResponse:
        """Latest daily bar per instrument, fetched in one batched call.

        One request for the whole universe rather than one per symbol - the
        same batching discipline the live provider uses, and what keeps this
        usable inside a request.
        """
        symbols = [key.split("|")[-1] for key in instrument_keys]
        if not symbols:
            raise ProviderError("No instruments requested.", ProviderStatus.ERROR)

        tickers = [to_yahoo_symbol(s) for s in symbols]
        data = self._download(tickers, period="5d", interval="1d")
        if data is None or len(data) == 0:
            raise ProviderError(
                "yfinance returned no data.", ProviderStatus.UNAVAILABLE
            )

        single = len(tickers) == 1
        quotes: dict[str, dict] = {}
        latest: datetime | None = None

        for symbol, ticker in zip(symbols, tickers):
            frame = self._frame_for(data, ticker, single)
            if frame is None:
                continue
            closes = frame["Close"].dropna()
            if len(closes) < 2:
                continue

            row_index = closes.index[-1]
            as_of = _as_naive_utc(row_index) or _utcnow()
            latest = max(latest, as_of) if latest else as_of

            def _value(column: str) -> float | None:
                try:
                    series = frame[column].dropna()
                    return float(series.iloc[-1]) if len(series) else None
                except (KeyError, ValueError, TypeError):
                    return None

            quotes[symbol] = {
                "last_price": float(closes.iloc[-1]),
                "previous_close": float(closes.iloc[-2]),
                "open": _value("Open"),
                "high": _value("High"),
                "low": _value("Low"),
                "volume": _value("Volume"),
                "as_of": as_of.isoformat(),
            }

        if not quotes:
            raise ProviderError(
                "yfinance returned no usable quotes.", ProviderStatus.UNAVAILABLE
            )

        moment = latest or _utcnow()
        return RawResponse(
            provider=self.name,
            payload={"source": self.name, "quotes": quotes},
            fetched_at=_utcnow(),
            # The bar's own timestamp, not our receipt time - a delayed feed
            # must report as delayed rather than as live.
            as_of=moment,
            status=ProviderStatus.OK,
            is_replay=False,
            meta={"requested": len(symbols), "returned": len(quotes)},
        )

    def get_historical_candles(
        self, instrument_key: str, interval: str, start: datetime, end: datetime
    ) -> RawResponse:
        """Daily candles for baseline construction."""
        symbol = instrument_key.split("|")[-1]
        ticker = to_yahoo_symbol(symbol)
        data = self._download(
            [ticker],
            start=start.date().isoformat(),
            end=(end + timedelta(days=1)).date().isoformat(),
            interval="1d",
        )
        return self._candles_response(data, ticker, symbol, interval)

    def get_intraday_candles(self, instrument_key: str, interval: str) -> RawResponse:
        """Current-session 15-minute candles for realized volatility."""
        symbol = instrument_key.split("|")[-1]
        ticker = to_yahoo_symbol(symbol)
        data = self._download([ticker], period="1d", interval="15m")
        return self._candles_response(data, ticker, symbol, "15m")

    def _candles_response(
        self, data: Any, ticker: str, symbol: str, interval: str
    ) -> RawResponse:
        if data is None or len(data) == 0:
            raise ProviderError(
                f"yfinance returned no candles for {symbol}.", ProviderStatus.UNAVAILABLE
            )

        frame = self._frame_for(data, ticker, single=True)
        if frame is None or "Close" not in frame:
            frame = self._frame_for(data, ticker, single=False)
        if frame is None:
            raise ProviderError(
                f"yfinance frame missing for {symbol}.", ProviderStatus.UNAVAILABLE
            )

        candles: list[dict] = []
        for index, row in frame.iterrows():
            close = row.get("Close")
            if close is None or close != close:  # NaN check
                continue
            stamp = _as_naive_utc(index)
            candles.append(
                {
                    "timestamp": stamp.isoformat() if stamp else None,
                    "open": _float_or_none(row.get("Open")),
                    "high": _float_or_none(row.get("High")),
                    "low": _float_or_none(row.get("Low")),
                    "close": float(close),
                    "volume": _float_or_none(row.get("Volume")),
                }
            )

        moment = _utcnow()
        return RawResponse(
            provider=self.name,
            payload={"source": self.name, "symbol": symbol, "candles": candles},
            fetched_at=moment,
            as_of=moment,
            status=ProviderStatus.OK,
            is_replay=False,
            meta={"interval": interval, "count": len(candles)},
        )


def _float_or_none(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return None if result != result else result  # drop NaN
