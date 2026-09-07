"""NSE provider via jugaad-data.

**This is not an official NSE API.** ``jugaad-data`` scrapes NSE's public web
endpoints. It is used here because it returns the exchange's own historical
data, but it is treated as best-effort throughout: NSE rate-limits, blocks
datacentre IPs, and changes response shapes without notice.

VERIFIED ON THIS MACHINE (development, not production):

* Historical daily OHLCV works and cross-checked exactly against Yahoo's close
  for the same session.
* ``NSELive.stock_quote`` returned a **stripped** ``priceInfo`` with no
  ``lastPrice`` field. That is why quotes here fall back to deriving the last
  price from the two most recent daily bars, and why the registry falls through
  to yfinance rather than treating this provider as authoritative for quotes.

Render's free tier runs in a datacentre, so NSE may block it outright in
production. That is expected and handled: the chain falls through automatically.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.providers.base import ProviderError, ProviderStatus, RawResponse

PROVIDER_NAME = "jugaad"

# jugaad-data addresses indices by name rather than symbol.
_INDEX_NAMES = {"NIFTY50": "NIFTY 50"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class JugaadDataProvider:
    """Best-effort NSE access. Never the last line of defence."""

    name = PROVIDER_NAME
    is_replay = False

    # NSE has no batch quote endpoint, so quotes cost one scrape per symbol.
    # Above this many symbols the wall-clock cost is unacceptable inside a
    # request, and the registry should fall through to yfinance's single
    # batched call instead. Historical seeding is unaffected: it is per-symbol
    # by nature and runs offline.
    MAX_QUOTE_BATCH = 3

    def __init__(self, lookback_days: int = 10) -> None:
        self.lookback_days = lookback_days

    def is_available(self) -> bool:
        try:
            import jugaad_data.nse  # noqa: F401
        except ImportError:
            return False
        return True

    # -- internals ---------------------------------------------------------
    @staticmethod
    def _stock_frame(symbol: str, start: date, end: date) -> Any:
        try:
            from jugaad_data.nse import stock_df
        except ImportError as exc:
            raise ProviderError(
                "jugaad-data is not installed.", ProviderStatus.UNAVAILABLE
            ) from exc
        try:
            return stock_df(symbol=symbol, from_date=start, to_date=end, series="EQ")
        except Exception as exc:
            # NSE blocks, rate-limits and reshapes without notice. Any failure
            # is reported as unavailable so the registry can fall through.
            raise ProviderError(
                f"NSE request failed for {symbol}: {exc}", ProviderStatus.UNAVAILABLE
            ) from exc

    @staticmethod
    def _rows(frame: Any) -> list[dict]:
        """Normalize jugaad's frame into ascending-by-date row dicts."""
        if frame is None or len(frame) == 0:
            return []

        rows: list[dict] = []
        for _, row in frame.iterrows():
            try:
                close = float(row["CLOSE"])
            except (KeyError, TypeError, ValueError):
                continue
            stamp = row.get("DATE")
            if isinstance(stamp, date) and not isinstance(stamp, datetime):
                stamp = datetime.combine(stamp, datetime.min.time())
            rows.append(
                {
                    "timestamp": stamp.isoformat() if isinstance(stamp, datetime) else None,
                    "open": _float(row.get("OPEN")),
                    "high": _float(row.get("HIGH")),
                    "low": _float(row.get("LOW")),
                    "close": close,
                    "previous_close": _float(row.get("PREV. CLOSE")),
                    "volume": _float(row.get("VOLUME")) or _float(row.get("NO OF TRADES")),
                }
            )
        # jugaad returns newest-first; the rest of LUMEN assumes ascending.
        rows.sort(key=lambda r: r["timestamp"] or "")
        return rows

    def _live_quote(self, symbol: str) -> dict | None:
        """Attempt NSE's live quote. Returns None whenever it is unusable.

        The endpoint frequently returns a partial ``priceInfo`` with no price
        at all, so every field is checked rather than assumed.
        """
        try:
            from jugaad_data.nse import NSELive

            payload = NSELive().stock_quote(symbol)
        except Exception:
            return None

        price_info = (payload or {}).get("priceInfo")
        if not isinstance(price_info, dict):
            return None
        last_price = price_info.get("lastPrice")
        if not isinstance(last_price, (int, float)) or last_price <= 0:
            return None

        return {
            "last_price": float(last_price),
            "previous_close": _float(price_info.get("previousClose")),
            "open": _float(price_info.get("open")),
            "high": _float((price_info.get("intraDayHighLow") or {}).get("max")),
            "low": _float((price_info.get("intraDayHighLow") or {}).get("min")),
            "volume": _float(
                ((payload.get("tradeInfo") or {}).get("marketDeptOrderBook") or {})
                .get("tradeInfo", {})
                .get("totalTradedVolume")
            ),
            "source_detail": "nse_live",
        }

    # -- provider interface -------------------------------------------------
    def get_quotes(self, instrument_keys: list[str]) -> RawResponse:
        """Quotes per instrument.

        NSE has no batch quote endpoint, so this loops. It is therefore
        appropriate for a background poll of a bounded universe, not for a
        per-request fan-out - the registry prefers yfinance's batched call
        when many symbols are needed.
        """
        symbols = [key.split("|")[-1] for key in instrument_keys]
        if not symbols:
            raise ProviderError("No instruments requested.", ProviderStatus.ERROR)

        if len(symbols) > self.MAX_QUOTE_BATCH:
            # Declining is the correct answer, not an error: scraping this many
            # symbols one at a time would block the request for tens of
            # seconds. The chain falls through to a provider that can batch.
            raise ProviderError(
                f"NSE has no batch quote endpoint; {len(symbols)} symbols exceeds "
                f"the {self.MAX_QUOTE_BATCH}-symbol limit for per-request use.",
                ProviderStatus.UNAVAILABLE,
            )

        end = date.today()
        start = end - timedelta(days=self.lookback_days)
        quotes: dict[str, dict] = {}
        failures = 0

        for symbol in symbols:
            live = self._live_quote(symbol)
            try:
                rows = self._rows(self._stock_frame(symbol, start, end))
            except ProviderError:
                failures += 1
                continue
            if not rows:
                failures += 1
                continue

            latest = rows[-1]
            previous_close = (
                latest.get("previous_close")
                or (rows[-2]["close"] if len(rows) >= 2 else None)
            )
            if not previous_close:
                failures += 1
                continue

            if live:
                # Live price where NSE actually supplies one; the daily bar
                # supplies the reference close either way.
                quotes[symbol] = {
                    **live,
                    "previous_close": live.get("previous_close") or previous_close,
                    "as_of": _utcnow().isoformat(),
                }
            else:
                quotes[symbol] = {
                    "last_price": latest["close"],
                    "previous_close": previous_close,
                    "open": latest["open"],
                    "high": latest["high"],
                    "low": latest["low"],
                    "volume": latest["volume"],
                    "as_of": latest["timestamp"] or _utcnow().isoformat(),
                    "source_detail": "nse_daily_close",
                }

        if not quotes:
            raise ProviderError(
                f"NSE returned no usable quotes ({failures} failures).",
                ProviderStatus.UNAVAILABLE,
            )

        as_of = max(
            (datetime.fromisoformat(q["as_of"]) for q in quotes.values() if q.get("as_of")),
            default=_utcnow(),
        )
        return RawResponse(
            provider=self.name,
            payload={"source": self.name, "quotes": quotes},
            fetched_at=_utcnow(),
            as_of=as_of,
            status=ProviderStatus.OK,
            is_replay=False,
            meta={"requested": len(symbols), "returned": len(quotes), "failures": failures},
        )

    def get_historical_candles(
        self, instrument_key: str, interval: str, start: datetime, end: datetime
    ) -> RawResponse:
        """Daily OHLCV straight from the exchange's own history."""
        symbol = instrument_key.split("|")[-1]
        if symbol in _INDEX_NAMES:
            return self._index_candles(symbol, start, end, interval)

        rows = self._rows(self._stock_frame(symbol, start.date(), end.date()))
        if not rows:
            raise ProviderError(
                f"NSE returned no history for {symbol}.", ProviderStatus.UNAVAILABLE
            )
        moment = _utcnow()
        return RawResponse(
            provider=self.name,
            payload={"source": self.name, "symbol": symbol, "candles": rows},
            fetched_at=moment,
            as_of=moment,
            status=ProviderStatus.OK,
            meta={"interval": interval, "count": len(rows)},
        )

    def _index_candles(
        self, symbol: str, start: datetime, end: datetime, interval: str
    ) -> RawResponse:
        """NIFTY benchmark history."""
        try:
            from jugaad_data.nse import index_df
        except ImportError as exc:
            raise ProviderError(
                "jugaad-data is not installed.", ProviderStatus.UNAVAILABLE
            ) from exc
        try:
            frame = index_df(
                symbol=_INDEX_NAMES[symbol], from_date=start.date(), to_date=end.date()
            )
        except Exception as exc:
            raise ProviderError(
                f"NSE index request failed: {exc}", ProviderStatus.UNAVAILABLE
            ) from exc

        candles: list[dict] = []
        for _, row in (frame or {}).iterrows() if frame is not None else []:
            close = _float(row.get("CLOSE"))
            if close is None:
                continue
            stamp = row.get("HistoricalDate") or row.get("DATE")
            candles.append(
                {
                    "timestamp": stamp.isoformat() if hasattr(stamp, "isoformat") else None,
                    "open": _float(row.get("OPEN")),
                    "high": _float(row.get("HIGH")),
                    "low": _float(row.get("LOW")),
                    "close": close,
                    "volume": None,
                }
            )
        candles.sort(key=lambda c: c["timestamp"] or "")

        moment = _utcnow()
        return RawResponse(
            provider=self.name,
            payload={"source": self.name, "symbol": symbol, "candles": candles},
            fetched_at=moment,
            as_of=moment,
            status=ProviderStatus.OK,
            meta={"interval": interval, "count": len(candles)},
        )

    def get_intraday_candles(self, instrument_key: str, interval: str) -> RawResponse:
        """Not supported.

        NSE's public endpoints do not expose reliable intraday history through
        this library. Raising rather than returning something thin keeps the
        volatility signal honestly unavailable instead of fabricated.
        """
        raise ProviderError(
            "Intraday candles are not available from the NSE provider.",
            ProviderStatus.UNAVAILABLE,
        )


def _float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return None if result != result else result
