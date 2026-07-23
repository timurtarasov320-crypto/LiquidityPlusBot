import asyncio
import json
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Optional

import aiohttp
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import ADMIN_ID
from project_paths import data_path
from order_flow import (
    OrderFlowResult,
    analyse_order_flow,
    format_money,
)
from signals import (
    create_signal,
    format_signal,
    get_signal,
    send_signal_to_users,
)

router = Router()

OKX_API_URL = "https://www.okx.com"

ASSISTANT_DB_NAME = "market_assistant.db"
SIGNALS_DB_NAME = data_path("signals.db")

MAX_MARKETS = 200
DEEP_ANALYSIS_LIMIT = 80
ORDER_FLOW_ANALYSIS_LIMIT = 20
MAX_RESULTS_TO_SHOW = 10

MIN_24H_VOLUME_USDT = 10_000_000
MAX_SPREAD_PERCENT = 0.20

MIN_PRELIMINARY_SCORE = 60
MIN_FINAL_SCORE = 72
STRONG_SETUP_SCORE = 84

SETUP_COOLDOWN_SECONDS = 4 * 60 * 60
REQUEST_CONCURRENCY = 3


@dataclass
class MarketCandidate:
    inst_id: str
    last_price: float
    volume_usdt: float
    change_24h: float
    bid_price: float
    ask_price: float
    spread_percent: float


@dataclass
class Setup:
    setup_id: str
    inst_id: str
    direction: str
    score: int

    entry_low: float
    entry_high: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    take_profit_3: float
    risk_reward: float

    rsi_15m: float
    rsi_1h: float
    rsi_4h: float

    ema_20_1h: float
    ema_50_1h: float
    ema_200_1h: float

    funding_rate: float
    open_interest: float
    spread_percent: float
    volume_ratio: float
    order_book_imbalance: float

    trend_15m: str
    trend_1h: str
    trend_4h: str
    btc_trend: str

    order_flow_score: int
    order_flow_direction: str
    order_flow_delta: float
    order_flow_delta_percent: float
    order_flow_cvd: float
    large_buy_volume: float
    large_sell_volume: float

    reasons: list[str]
    warnings: list[str]


@dataclass
class GlobalMarketContext:
    fear_greed: int = 50
    fear_greed_label: str = "Neutral"
    altseason_proxy: int = 50
    btc_change_24h: float = 0.0


def is_admin(user_id: int) -> bool:
    return int(user_id) == int(ADMIN_ID)


def connect_assistant_db() -> sqlite3.Connection:
    connection = sqlite3.connect(ASSISTANT_DB_NAME)
    connection.row_factory = sqlite3.Row
    return connection


def create_assistant_tables() -> None:
    connection = connect_assistant_db()
    cursor = connection.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS assistant_setups (
            setup_id TEXT PRIMARY KEY,
            inst_id TEXT NOT NULL,
            direction TEXT NOT NULL,
            score INTEGER NOT NULL,
            setup_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at INTEGER NOT NULL,
            published_at INTEGER DEFAULT NULL
        )
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_assistant_market_time
        ON assistant_setups(inst_id, created_at)
        """
    )

    connection.commit()
    connection.close()


def save_setup(setup: Setup) -> None:
    connection = connect_assistant_db()
    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT OR REPLACE INTO assistant_setups (
            setup_id,
            inst_id,
            direction,
            score,
            setup_json,
            status,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, 'pending', ?)
        """,
        (
            setup.setup_id,
            setup.inst_id,
            setup.direction,
            setup.score,
            json.dumps(asdict(setup), ensure_ascii=False),
            int(time.time()),
        ),
    )

    connection.commit()
    connection.close()


def get_saved_setup(setup_id: str) -> Optional[Setup]:
    connection = connect_assistant_db()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT setup_json
        FROM assistant_setups
        WHERE setup_id = ?
        """,
        (setup_id,),
    )

    result = cursor.fetchone()
    connection.close()

    if result is None:
        return None

    try:
        payload = json.loads(result["setup_json"])
        return Setup(**payload)
    except (json.JSONDecodeError, TypeError):
        return None


def update_setup_status(
    setup_id: str,
    status: str,
) -> None:
    connection = connect_assistant_db()
    cursor = connection.cursor()

    published_at = (
        int(time.time())
        if status == "published"
        else None
    )

    cursor.execute(
        """
        UPDATE assistant_setups
        SET
            status = ?,
            published_at = COALESCE(?, published_at)
        WHERE setup_id = ?
        """,
        (
            status,
            published_at,
            setup_id,
        ),
    )

    connection.commit()
    connection.close()


def is_setup_on_cooldown(inst_id: str) -> bool:
    threshold = (
        int(time.time())
        - SETUP_COOLDOWN_SECONDS
    )

    connection = connect_assistant_db()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT setup_id
        FROM assistant_setups
        WHERE inst_id = ?
          AND created_at >= ?
          AND status IN ('pending', 'published')
        LIMIT 1
        """,
        (
            inst_id,
            threshold,
        ),
    )

    result = cursor.fetchone()
    connection.close()

    return result is not None


def has_active_signal(inst_id: str) -> bool:
    symbol = inst_id.replace(
        "-USDT-SWAP",
        "/USDT",
    )

    try:
        connection = sqlite3.connect(SIGNALS_DB_NAME)
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT signal_id
            FROM signals
            WHERE UPPER(symbol) = UPPER(?)
              AND status = 'active'
            LIMIT 1
            """,
            (symbol,),
        )

        result = cursor.fetchone()
        connection.close()

        return result is not None

    except sqlite3.Error:
        return False


class OKXClient:
    def __init__(self) -> None:
        self.timeout = aiohttp.ClientTimeout(total=25)
        self.semaphore = asyncio.Semaphore(
            REQUEST_CONCURRENCY
        )
        self.session: Optional[
            aiohttp.ClientSession
        ] = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(
            timeout=self.timeout
        )
        return self

    async def __aexit__(
        self,
        exc_type,
        exc,
        traceback,
    ):
        if self.session is not None:
            await self.session.close()

    async def get(
        self,
        endpoint: str,
        params: Optional[dict[str, Any]] = None,
        retries: int = 4,
    ) -> list[dict[str, Any]]:
        if self.session is None:
            raise RuntimeError(
                "HTTP-сессия OKX не создана."
            )

        url = f"{OKX_API_URL}{endpoint}"

        for attempt in range(retries):
            async with self.semaphore:
                try:
                    async with self.session.get(
                        url,
                        params=params or {},
                    ) as response:
                        if response.status == 429:
                            raise RuntimeError(
                                "OKX HTTP 429"
                            )

                        response.raise_for_status()
                        payload = await response.json()

                    code = str(payload.get("code", ""))

                    if code == "0":
                        return payload.get("data", [])

                    if code == "50011":
                        raise RuntimeError(
                            "OKX rate limit 50011"
                        )

                    raise RuntimeError(
                        payload.get(
                            "msg",
                            "Неизвестная ошибка OKX",
                        )
                    )

                except (
                    aiohttp.ClientError,
                    asyncio.TimeoutError,
                    RuntimeError,
                ) as error:
                    if attempt >= retries - 1:
                        raise RuntimeError(
                            f"Запрос OKX не выполнен: {error}"
                        ) from error

                    await asyncio.sleep(
                        1.5 * (attempt + 1)
                    )

        return []


async def get_top_swap_markets(
    client: OKXClient,
) -> list[MarketCandidate]:
    tickers = await client.get(
        "/api/v5/market/tickers",
        {"instType": "SWAP"},
    )

    candidates: list[MarketCandidate] = []

    for ticker in tickers:
        inst_id = str(
            ticker.get("instId", "")
        )

        if not inst_id.endswith("-USDT-SWAP"):
            continue

        try:
            last_price = float(
                ticker.get("last") or 0
            )
            open_24h = float(
                ticker.get("open24h") or 0
            )
            bid_price = float(
                ticker.get("bidPx") or 0
            )
            ask_price = float(
                ticker.get("askPx") or 0
            )
            volume_usdt = float(
                ticker.get("volCcy24h") or 0
            )
        except (TypeError, ValueError):
            continue

        if (
            last_price <= 0
            or bid_price <= 0
            or ask_price <= 0
            or volume_usdt
            < MIN_24H_VOLUME_USDT
        ):
            continue

        middle_price = (
            bid_price + ask_price
        ) / 2

        if middle_price <= 0:
            continue

        spread_percent = (
            (ask_price - bid_price)
            / middle_price
            * 100
        )

        if spread_percent > MAX_SPREAD_PERCENT:
            continue

        change_24h = 0.0

        if open_24h > 0:
            change_24h = (
                (last_price - open_24h)
                / open_24h
                * 100
            )

        candidates.append(
            MarketCandidate(
                inst_id=inst_id,
                last_price=last_price,
                volume_usdt=volume_usdt,
                change_24h=change_24h,
                bid_price=bid_price,
                ask_price=ask_price,
                spread_percent=spread_percent,
            )
        )

    candidates.sort(
        key=lambda market: market.volume_usdt,
        reverse=True,
    )

    return candidates[:MAX_MARKETS]


async def get_candles(
    client: OKXClient,
    inst_id: str,
    bar: str,
    limit: int = 240,
) -> list[dict[str, float]]:
    raw = await client.get(
        "/api/v5/market/candles",
        {
            "instId": inst_id,
            "bar": bar,
            "limit": str(limit),
        },
    )

    candles: list[dict[str, float]] = []

    for item in reversed(raw):
        try:
            candles.append(
                {
                    "timestamp": float(item[0]),
                    "open": float(item[1]),
                    "high": float(item[2]),
                    "low": float(item[3]),
                    "close": float(item[4]),
                    "volume": float(item[5]),
                    "volume_ccy": float(item[6]),
                }
            )
        except (
            IndexError,
            TypeError,
            ValueError,
        ):
            continue

    return candles


async def get_funding_rate(
    client: OKXClient,
    inst_id: str,
) -> float:
    data = await client.get(
        "/api/v5/public/funding-rate",
        {"instId": inst_id},
    )

    if not data:
        return 0.0

    try:
        return float(
            data[0].get("fundingRate") or 0
        )
    except (TypeError, ValueError):
        return 0.0


async def get_open_interest(
    client: OKXClient,
    inst_id: str,
) -> float:
    data = await client.get(
        "/api/v5/public/open-interest",
        {
            "instType": "SWAP",
            "instId": inst_id,
        },
    )

    if not data:
        return 0.0

    try:
        return float(
            data[0].get("oiCcy")
            or data[0].get("oi")
            or 0
        )
    except (TypeError, ValueError):
        return 0.0


async def get_order_book_metrics(
    client: OKXClient,
    inst_id: str,
) -> tuple[float, float]:
    data = await client.get(
        "/api/v5/market/books",
        {
            "instId": inst_id,
            "sz": "20",
        },
    )

    if not data:
        return 0.0, 0.5

    asks = data[0].get("asks", [])
    bids = data[0].get("bids", [])

    if not asks or not bids:
        return 0.0, 0.5

    try:
        best_ask = float(asks[0][0])
        best_bid = float(bids[0][0])

        ask_depth = sum(
            float(level[0]) * float(level[1])
            for level in asks
        )

        bid_depth = sum(
            float(level[0]) * float(level[1])
            for level in bids
        )
    except (
        IndexError,
        TypeError,
        ValueError,
    ):
        return 0.0, 0.5

    middle = (best_ask + best_bid) / 2

    spread_percent = (
        (best_ask - best_bid)
        / middle
        * 100
        if middle > 0
        else 0.0
    )

    total_depth = bid_depth + ask_depth

    imbalance = (
        bid_depth / total_depth
        if total_depth > 0
        else 0.5
    )

    return spread_percent, imbalance


def ema(
    values: list[float],
    period: int,
) -> float:
    if not values:
        return 0.0

    if len(values) < period:
        return sum(values) / len(values)

    multiplier = 2 / (period + 1)
    result = sum(values[:period]) / period

    for value in values[period:]:
        result = (
            value - result
        ) * multiplier + result

    return result


def calculate_rsi(
    values: list[float],
    period: int = 14,
) -> float:
    if len(values) <= period:
        return 50.0

    gains: list[float] = []
    losses: list[float] = []

    for index in range(1, period + 1):
        change = (
            values[index]
            - values[index - 1]
        )

        gains.append(max(change, 0))
        losses.append(abs(min(change, 0)))

    average_gain = sum(gains) / period
    average_loss = sum(losses) / period

    for index in range(
        period + 1,
        len(values),
    ):
        change = (
            values[index]
            - values[index - 1]
        )

        gain = max(change, 0)
        loss = abs(min(change, 0))

        average_gain = (
            average_gain * (period - 1)
            + gain
        ) / period

        average_loss = (
            average_loss * (period - 1)
            + loss
        ) / period

    if average_loss == 0:
        return 100.0

    relative_strength = (
        average_gain / average_loss
    )

    return 100 - (
        100 / (1 + relative_strength)
    )


def calculate_atr(
    candles: list[dict[str, float]],
    period: int = 14,
) -> float:
    if len(candles) <= period:
        return 0.0

    true_ranges: list[float] = []

    for index in range(1, len(candles)):
        current = candles[index]
        previous = candles[index - 1]

        true_range = max(
            current["high"] - current["low"],
            abs(
                current["high"]
                - previous["close"]
            ),
            abs(
                current["low"]
                - previous["close"]
            ),
        )

        true_ranges.append(true_range)

    return (
        sum(true_ranges[-period:])
        / period
    )


def calculate_volume_ratio(
    candles: list[dict[str, float]],
    period: int = 20,
) -> float:
    if len(candles) < period + 1:
        return 1.0

    current_volume = candles[-1]["volume"]

    average_volume = sum(
        candle["volume"]
        for candle in candles[
            -period - 1:-1
        ]
    ) / period

    if average_volume <= 0:
        return 1.0

    return current_volume / average_volume


def detect_trend(
    candles: list[dict[str, float]],
) -> str:
    closes = [
        candle["close"]
        for candle in candles
    ]

    if len(closes) < 200:
        return "neutral"

    ema_20_value = ema(closes, 20)
    ema_50_value = ema(closes, 50)
    ema_200_value = ema(closes, 200)
    price = closes[-1]

    if (
        price > ema_20_value
        > ema_50_value
        > ema_200_value
    ):
        return "strong_bullish"

    if (
        price < ema_20_value
        < ema_50_value
        < ema_200_value
    ):
        return "strong_bearish"

    if (
        price > ema_20_value
        and ema_20_value > ema_50_value
    ):
        return "bullish"

    if (
        price < ema_20_value
        and ema_20_value < ema_50_value
    ):
        return "bearish"

    return "neutral"


def detect_abnormal_candle(
    candles: list[dict[str, float]],
) -> bool:
    if len(candles) < 25:
        return False

    previous_ranges = [
        candle["high"] - candle["low"]
        for candle in candles[-21:-1]
    ]

    average_range = (
        sum(previous_ranges)
        / len(previous_ranges)
    )

    current_range = (
        candles[-1]["high"]
        - candles[-1]["low"]
    )

    if average_range <= 0:
        return False

    return current_range >= average_range * 3


def local_levels(
    candles: list[dict[str, float]],
    lookback: int = 40,
) -> tuple[float, float]:
    recent = candles[-lookback:]

    support = min(
        candle["low"]
        for candle in recent
    )

    resistance = max(
        candle["high"]
        for candle in recent
    )

    return support, resistance



def calculate_altseason_proxy(
    markets: list[MarketCandidate],
) -> int:
    """Доля крупных альткоинов, которые сильнее BTC за 24 часа."""
    btc_change = 0.0

    for market in markets:
        if market.inst_id == "BTC-USDT-SWAP":
            btc_change = market.change_24h
            break

    alts = [
        market
        for market in markets
        if market.inst_id != "BTC-USDT-SWAP"
    ][:50]

    if not alts:
        return 50

    outperforming = sum(
        1
        for market in alts
        if market.change_24h > btc_change
    )

    return round(outperforming / len(alts) * 100)


async def get_fear_greed_index() -> tuple[int, str]:
    url = "https://api.alternative.me/fng/"

    timeout = aiohttp.ClientTimeout(total=10)

    try:
        async with aiohttp.ClientSession(
            timeout=timeout
        ) as session:
            async with session.get(
                url,
                params={"limit": 1, "format": "json"},
            ) as response:
                response.raise_for_status()
                payload = await response.json()

        item = payload.get("data", [{}])[0]
        value = int(item.get("value", 50))
        label = str(
            item.get(
                "value_classification",
                "Neutral",
            )
        )
        return max(0, min(value, 100)), label

    except Exception as error:
        print(f"Fear & Greed недоступен: {error}")
        return 50, "Neutral"


def detect_fvg(
    candles: list[dict[str, float]],
    current_price: float,
) -> tuple[str, float | None, float | None]:
    """Ищет ближайший трёхсвечный дисбаланс."""
    bullish: list[tuple[float, float]] = []
    bearish: list[tuple[float, float]] = []

    for index in range(2, len(candles)):
        first = candles[index - 2]
        third = candles[index]

        if first["high"] < third["low"]:
            bullish.append(
                (first["high"], third["low"])
            )

        if first["low"] > third["high"]:
            bearish.append(
                (third["high"], first["low"])
            )

    candidates: list[
        tuple[float, str, float, float]
    ] = []

    for low, high in bullish[-20:]:
        midpoint = (low + high) / 2
        distance = abs(current_price - midpoint)
        candidates.append(
            (distance, "bullish", low, high)
        )

    for low, high in bearish[-20:]:
        midpoint = (low + high) / 2
        distance = abs(current_price - midpoint)
        candidates.append(
            (distance, "bearish", low, high)
        )

    if not candidates:
        return "none", None, None

    _, direction, low, high = min(
        candidates,
        key=lambda item: item[0],
    )

    return direction, low, high


def detect_liquidity_sweep(
    candles: list[dict[str, float]],
    lookback: int = 20,
) -> str:
    if len(candles) < lookback + 2:
        return "none"

    latest = candles[-1]
    previous = candles[-lookback - 1:-1]

    previous_high = max(
        candle["high"]
        for candle in previous
    )
    previous_low = min(
        candle["low"]
        for candle in previous
    )

    if (
        latest["high"] > previous_high
        and latest["close"] < previous_high
    ):
        return "high_sweep"

    if (
        latest["low"] < previous_low
        and latest["close"] > previous_low
    ):
        return "low_sweep"

    return "none"


def detect_order_block(
    candles: list[dict[str, float]],
    direction: str,
) -> tuple[float | None, float | None]:
    """Упрощённый OB: последняя противоположная свеча перед импульсом."""
    if len(candles) < 30:
        return None, None

    atr_value = calculate_atr(candles[-30:])

    if atr_value <= 0:
        return None, None

    for index in range(
        len(candles) - 3,
        max(1, len(candles) - 25),
        -1,
    ):
        candle = candles[index]
        next_candle = candles[index + 1]
        next_range = (
            next_candle["high"]
            - next_candle["low"]
        )

        if next_range < atr_value * 1.35:
            continue

        if (
            direction == "LONG"
            and candle["close"] < candle["open"]
            and next_candle["close"] > candle["high"]
        ):
            return candle["low"], candle["high"]

        if (
            direction == "SHORT"
            and candle["close"] > candle["open"]
            and next_candle["close"] < candle["low"]
        ):
            return candle["low"], candle["high"]

    return None, None


def apply_smart_money_filters(
    setup: Setup,
    candles_15m: list[dict[str, float]],
    candles_1h: list[dict[str, float]],
    context: GlobalMarketContext,
) -> Setup:
    current_price = candles_15m[-1]["close"]

    fvg_direction, fvg_low, fvg_high = detect_fvg(
        candles_15m,
        current_price,
    )
    sweep = detect_liquidity_sweep(
        candles_15m
    )
    ob_low, ob_high = detect_order_block(
        candles_1h,
        setup.direction,
    )

    if fvg_direction == "bullish":
        if setup.direction == "LONG":
            setup.score += 6
            setup.reasons.append(
                "Bullish FVG рядом с текущей ценой"
            )
        else:
            setup.score -= 3
            setup.warnings.append(
                "Рядом находится bullish FVG"
            )

    if fvg_direction == "bearish":
        if setup.direction == "SHORT":
            setup.score += 6
            setup.reasons.append(
                "Bearish FVG рядом с текущей ценой"
            )
        else:
            setup.score -= 3
            setup.warnings.append(
                "Рядом находится bearish FVG"
            )

    if fvg_low is not None and fvg_high is not None:
        setup.reasons.append(
            "Зона FVG: "
            f"{price_text(fvg_low)}–"
            f"{price_text(fvg_high)}"
        )

    if sweep == "low_sweep":
        if setup.direction == "LONG":
            setup.score += 8
            setup.reasons.append(
                "Снята ликвидность под локальным минимумом"
            )
        else:
            setup.score -= 5
            setup.warnings.append(
                "Зафиксирован sweep нижней ликвидности"
            )

    if sweep == "high_sweep":
        if setup.direction == "SHORT":
            setup.score += 8
            setup.reasons.append(
                "Снята ликвидность над локальным максимумом"
            )
        else:
            setup.score -= 5
            setup.warnings.append(
                "Зафиксирован sweep верхней ликвидности"
            )

    if ob_low is not None and ob_high is not None:
        setup.score += 5
        setup.reasons.append(
            f"{setup.direction} Order Block: "
            f"{price_text(ob_low)}–"
            f"{price_text(ob_high)}"
        )

    if context.fear_greed <= 25:
        if setup.direction == "LONG":
            setup.score += 4
            setup.reasons.append(
                "Fear & Greed: экстремальный страх"
            )
        else:
            setup.warnings.append(
                "SHORT открывается при экстремальном страхе"
            )

    if context.fear_greed >= 75:
        if setup.direction == "SHORT":
            setup.score += 4
            setup.reasons.append(
                "Fear & Greed: экстремальная жадность"
            )
        else:
            setup.warnings.append(
                "LONG открывается при экстремальной жадности"
            )

    if context.altseason_proxy >= 60:
        if setup.direction == "LONG":
            setup.score += 4
            setup.reasons.append(
                "Altseason proxy поддерживает альткоины"
            )
    elif context.altseason_proxy <= 35:
        if (
            setup.direction == "SHORT"
            and setup.inst_id != "BTC-USDT-SWAP"
        ):
            setup.score += 3
            setup.reasons.append(
                "Слабая ширина рынка альткоинов"
            )

    setup.reasons.append(
        "Fear & Greed: "
        f"{context.fear_greed}/100 "
        f"({context.fear_greed_label})"
    )
    setup.reasons.append(
        "Altseason proxy: "
        f"{context.altseason_proxy}/100"
    )

    setup.score = max(
        0,
        min(setup.score, 100),
    )

    return setup


def create_preliminary_setup(
    candidate: MarketCandidate,
    candles_15m: list[dict[str, float]],
    candles_1h: list[dict[str, float]],
    candles_4h: list[dict[str, float]],
    funding_rate: float,
    open_interest: float,
    book_spread: float,
    bid_imbalance: float,
    btc_trend: str,
    global_context: GlobalMarketContext,
) -> Optional[Setup]:
    if (
        len(candles_15m) < 200
        or len(candles_1h) < 200
        or len(candles_4h) < 200
    ):
        return None

    if detect_abnormal_candle(candles_15m):
        return None

    closes_15m = [
        candle["close"]
        for candle in candles_15m
    ]

    closes_1h = [
        candle["close"]
        for candle in candles_1h
    ]

    closes_4h = [
        candle["close"]
        for candle in candles_4h
    ]

    rsi_15m = calculate_rsi(closes_15m)
    rsi_1h = calculate_rsi(closes_1h)
    rsi_4h = calculate_rsi(closes_4h)

    trend_15m = detect_trend(candles_15m)
    trend_1h = detect_trend(candles_1h)
    trend_4h = detect_trend(candles_4h)

    ema_20_1h = ema(closes_1h, 20)
    ema_50_1h = ema(closes_1h, 50)
    ema_200_1h = ema(closes_1h, 200)

    atr_1h = calculate_atr(candles_1h)

    volume_ratio = calculate_volume_ratio(
        candles_15m
    )

    current_price = closes_15m[-1]

    if current_price <= 0 or atr_1h <= 0:
        return None

    support, resistance = local_levels(
        candles_1h
    )

    long_score = 0
    short_score = 0

    long_reasons: list[str] = []
    short_reasons: list[str] = []
    warnings: list[str] = []

    if trend_4h == "strong_bullish":
        long_score += 22
        long_reasons.append(
            "Сильный восходящий тренд на 4H"
        )
    elif trend_4h == "bullish":
        long_score += 15
        long_reasons.append(
            "Восходящий тренд на 4H"
        )

    if trend_4h == "strong_bearish":
        short_score += 22
        short_reasons.append(
            "Сильный нисходящий тренд на 4H"
        )
    elif trend_4h == "bearish":
        short_score += 15
        short_reasons.append(
            "Нисходящий тренд на 4H"
        )

    if trend_1h == "strong_bullish":
        long_score += 20
        long_reasons.append(
            "EMA20 > EMA50 > EMA200 на 1H"
        )
    elif trend_1h == "bullish":
        long_score += 14
        long_reasons.append(
            "EMA20 выше EMA50 на 1H"
        )

    if trend_1h == "strong_bearish":
        short_score += 20
        short_reasons.append(
            "EMA20 < EMA50 < EMA200 на 1H"
        )
    elif trend_1h == "bearish":
        short_score += 14
        short_reasons.append(
            "EMA20 ниже EMA50 на 1H"
        )

    if trend_15m in (
        "bullish",
        "strong_bullish",
    ):
        long_score += 8

    if trend_15m in (
        "bearish",
        "strong_bearish",
    ):
        short_score += 8

    if 38 <= rsi_1h <= 58:
        long_score += 10
        long_reasons.append(
            f"RSI 1H подходит для LONG: {rsi_1h:.1f}"
        )

    if 42 <= rsi_1h <= 62:
        short_score += 10
        short_reasons.append(
            f"RSI 1H подходит для SHORT: {rsi_1h:.1f}"
        )

    if rsi_15m <= 35:
        long_score += 9
        long_reasons.append(
            f"RSI 15m перепродан: {rsi_15m:.1f}"
        )

    if rsi_15m >= 65:
        short_score += 9
        short_reasons.append(
            f"RSI 15m перекуплен: {rsi_15m:.1f}"
        )

    if volume_ratio >= 1.40:
        long_score += 7
        short_score += 7

        reason = (
            "Объём выше среднего "
            f"в {volume_ratio:.2f} раза"
        )

        long_reasons.append(reason)
        short_reasons.append(reason)

    if funding_rate <= -0.0003:
        long_score += 8
        long_reasons.append(
            "Отрицательный funding: "
            f"{funding_rate * 100:.4f}%"
        )

    if funding_rate >= 0.0003:
        short_score += 8
        short_reasons.append(
            "Положительный funding: "
            f"{funding_rate * 100:.4f}%"
        )

    support_distance = (
        current_price - support
    ) / current_price

    resistance_distance = (
        resistance - current_price
    ) / current_price

    if 0 <= support_distance <= 0.025:
        long_score += 9
        long_reasons.append(
            "Цена находится возле поддержки"
        )

    if 0 <= resistance_distance <= 0.025:
        short_score += 9
        short_reasons.append(
            "Цена находится возле сопротивления"
        )

    if bid_imbalance >= 0.58:
        long_score += 6
        long_reasons.append(
            "В стакане преобладают покупатели"
        )

    if bid_imbalance <= 0.42:
        short_score += 6
        short_reasons.append(
            "В стакане преобладают продавцы"
        )

    effective_spread = max(
        candidate.spread_percent,
        book_spread,
    )

    if effective_spread <= 0.05:
        long_score += 4
        short_score += 4

    if effective_spread > 0.15:
        long_score -= 8
        short_score -= 8
        warnings.append("Повышенный спред")

    if open_interest > 0:
        long_score += 2
        short_score += 2

    is_btc = (
        candidate.inst_id
        == "BTC-USDT-SWAP"
    )

    if not is_btc:
        if btc_trend in (
            "bullish",
            "strong_bullish",
        ):
            long_score += 8
            short_score -= 6

            long_reasons.append(
                "Тренд BTC поддерживает LONG"
            )

        if btc_trend in (
            "bearish",
            "strong_bearish",
        ):
            short_score += 8
            long_score -= 6

            short_reasons.append(
                "Тренд BTC поддерживает SHORT"
            )

    if abs(candidate.change_24h) >= 20:
        long_score -= 8
        short_score -= 8

        warnings.append(
            "Цена сильно изменилась за 24 часа"
        )

    if long_score >= short_score:
        direction = "LONG"
        score = max(
            0,
            min(long_score, 100),
        )
        reasons = long_reasons

        entry_low = (
            current_price - atr_1h * 0.18
        )
        entry_high = (
            current_price + atr_1h * 0.08
        )
        stop_loss = (
            current_price - atr_1h * 1.30
        )

        risk_distance = (
            current_price - stop_loss
        )

        take_profit_1 = (
            current_price
            + risk_distance * 1.5
        )
        take_profit_2 = (
            current_price
            + risk_distance * 2.2
        )
        take_profit_3 = (
            current_price
            + risk_distance * 3.0
        )

    else:
        direction = "SHORT"
        score = max(
            0,
            min(short_score, 100),
        )
        reasons = short_reasons

        entry_low = (
            current_price - atr_1h * 0.08
        )
        entry_high = (
            current_price + atr_1h * 0.18
        )
        stop_loss = (
            current_price + atr_1h * 1.30
        )

        risk_distance = (
            stop_loss - current_price
        )

        take_profit_1 = (
            current_price
            - risk_distance * 1.5
        )
        take_profit_2 = (
            current_price
            - risk_distance * 2.2
        )
        take_profit_3 = (
            current_price
            - risk_distance * 3.0
        )

    if risk_distance <= 0:
        return None

    reward_distance = abs(
        take_profit_2 - current_price
    )

    risk_reward = (
        reward_distance / risk_distance
    )

    if (
        score < MIN_PRELIMINARY_SCORE
        or risk_reward < 2
    ):
        return None

    if min(
        stop_loss,
        take_profit_1,
        take_profit_2,
        take_profit_3,
    ) <= 0:
        return None

    setup = Setup(
        setup_id=uuid.uuid4().hex[:10],
        inst_id=candidate.inst_id,
        direction=direction,
        score=score,
        entry_low=entry_low,
        entry_high=entry_high,
        stop_loss=stop_loss,
        take_profit_1=take_profit_1,
        take_profit_2=take_profit_2,
        take_profit_3=take_profit_3,
        risk_reward=risk_reward,
        rsi_15m=rsi_15m,
        rsi_1h=rsi_1h,
        rsi_4h=rsi_4h,
        ema_20_1h=ema_20_1h,
        ema_50_1h=ema_50_1h,
        ema_200_1h=ema_200_1h,
        funding_rate=funding_rate,
        open_interest=open_interest,
        spread_percent=effective_spread,
        volume_ratio=volume_ratio,
        order_book_imbalance=bid_imbalance,
        trend_15m=trend_15m,
        trend_1h=trend_1h,
        trend_4h=trend_4h,
        btc_trend=btc_trend,
        order_flow_score=0,
        order_flow_direction="not_checked",
        order_flow_delta=0,
        order_flow_delta_percent=0,
        order_flow_cvd=0,
        large_buy_volume=0,
        large_sell_volume=0,
        reasons=reasons[:10],
        warnings=warnings,
    )

    return apply_smart_money_filters(
        setup=setup,
        candles_15m=candles_15m,
        candles_1h=candles_1h,
        context=global_context,
    )


def apply_order_flow(
    setup: Setup,
    order_flow: Optional[OrderFlowResult],
) -> Setup:
    if order_flow is None:
        setup.warnings.append(
            "Order Flow не удалось рассчитать"
        )
        return setup

    setup.order_flow_score = order_flow.score
    setup.order_flow_direction = (
        order_flow.direction
    )
    setup.order_flow_delta = order_flow.delta
    setup.order_flow_delta_percent = (
        order_flow.delta_percent
    )
    setup.order_flow_cvd = order_flow.cvd
    setup.large_buy_volume = (
        order_flow.large_buy_volume
    )
    setup.large_sell_volume = (
        order_flow.large_sell_volume
    )

    direction_matches = (
        setup.direction == "LONG"
        and order_flow.direction == "bullish"
    ) or (
        setup.direction == "SHORT"
        and order_flow.direction == "bearish"
    )

    direction_conflicts = (
        setup.direction == "LONG"
        and order_flow.direction == "bearish"
    ) or (
        setup.direction == "SHORT"
        and order_flow.direction == "bullish"
    )

    order_flow_strength = abs(
        order_flow.score
    )

    if direction_matches:
        if order_flow_strength >= 60:
            setup.score += 18
        elif order_flow_strength >= 35:
            setup.score += 12
        else:
            setup.score += 6

        setup.reasons.append(
            "Order Flow подтверждает направление"
        )

    elif direction_conflicts:
        if order_flow_strength >= 60:
            setup.score -= 25
        elif order_flow_strength >= 35:
            setup.score -= 16
        else:
            setup.score -= 8

        setup.warnings.append(
            "Order Flow противоречит направлению"
        )

    else:
        setup.warnings.append(
            "Order Flow нейтральный"
        )

    if setup.direction == "LONG":
        if (
            order_flow.large_buy_volume
            > order_flow.large_sell_volume
        ):
            setup.score += 5
            setup.reasons.append(
                "Крупные покупки сильнее крупных продаж"
            )
        else:
            setup.score -= 4

    if setup.direction == "SHORT":
        if (
            order_flow.large_sell_volume
            > order_flow.large_buy_volume
        ):
            setup.score += 5
            setup.reasons.append(
                "Крупные продажи сильнее крупных покупок"
            )
        else:
            setup.score -= 4

    setup.score = max(
        0,
        min(setup.score, 100),
    )

    setup.reasons.extend(
        order_flow.reasons[:3]
    )

    setup.warnings.extend(
        order_flow.warnings[:2]
    )

    return setup


async def get_btc_trend(
    client: OKXClient,
) -> str:
    candles = await get_candles(
        client,
        "BTC-USDT-SWAP",
        "1H",
    )

    return detect_trend(candles)


async def analyse_candidate(
    client: OKXClient,
    candidate: MarketCandidate,
    btc_trend: str,
    global_context: GlobalMarketContext,
) -> Optional[Setup]:
    if has_active_signal(candidate.inst_id):
        return None

    if is_setup_on_cooldown(candidate.inst_id):
        return None

    (
        candles_15m,
        candles_1h,
        candles_4h,
        funding_rate,
        open_interest,
        book_metrics,
    ) = await asyncio.gather(
        get_candles(
            client,
            candidate.inst_id,
            "15m",
        ),
        get_candles(
            client,
            candidate.inst_id,
            "1H",
        ),
        get_candles(
            client,
            candidate.inst_id,
            "4H",
        ),
        get_funding_rate(
            client,
            candidate.inst_id,
        ),
        get_open_interest(
            client,
            candidate.inst_id,
        ),
        get_order_book_metrics(
            client,
            candidate.inst_id,
        ),
    )

    book_spread, bid_imbalance = (
        book_metrics
    )

    return create_preliminary_setup(
        candidate=candidate,
        candles_15m=candles_15m,
        candles_1h=candles_1h,
        candles_4h=candles_4h,
        funding_rate=funding_rate,
        open_interest=open_interest,
        book_spread=book_spread,
        bid_imbalance=bid_imbalance,
        btc_trend=btc_trend,
        global_context=global_context,
    )


async def scan_markets(
    progress_callback=None,
) -> tuple[list[Setup], int]:
    async with OKXClient() as client:
        markets = await get_top_swap_markets(
            client
        )

        selected = markets[
            :DEEP_ANALYSIS_LIMIT
        ]

        btc_trend = await get_btc_trend(
            client
        )

        fear_greed, fear_greed_label = (
            await get_fear_greed_index()
        )

        global_context = GlobalMarketContext(
            fear_greed=fear_greed,
            fear_greed_label=fear_greed_label,
            altseason_proxy=calculate_altseason_proxy(
                selected
            ),
            btc_change_24h=next(
                (
                    market.change_24h
                    for market in selected
                    if market.inst_id
                    == "BTC-USDT-SWAP"
                ),
                0.0,
            ),
        )

        preliminary_setups: list[Setup] = []
        completed = 0
        lock = asyncio.Lock()

        async def run_candidate(
            candidate: MarketCandidate,
        ) -> None:
            nonlocal completed

            try:
                setup = await analyse_candidate(
                    client,
                    candidate,
                    btc_trend,
                    global_context,
                )

                if setup is not None:
                    preliminary_setups.append(
                        setup
                    )

            except Exception as error:
                print(
                    "Ошибка анализа "
                    f"{candidate.inst_id}: {error}"
                )

            async with lock:
                completed += 1

                if (
                    progress_callback
                    and completed % 10 == 0
                ):
                    await progress_callback(
                        "technical",
                        completed,
                        len(selected),
                    )

        batch_size = 5

        for start_index in range(
            0,
            len(selected),
            batch_size,
        ):
            batch = selected[
                start_index:
                start_index + batch_size
            ]

            await asyncio.gather(
                *[
                    run_candidate(candidate)
                    for candidate in batch
                ]
            )

            await asyncio.sleep(0.8)

    preliminary_setups.sort(
        key=lambda item: item.score,
        reverse=True,
    )

    order_flow_targets = preliminary_setups[
        :ORDER_FLOW_ANALYSIS_LIMIT
    ]

    final_setups: list[Setup] = []

    for index, setup in enumerate(
        order_flow_targets,
        start=1,
    ):
        try:
            order_flow = await analyse_order_flow(
                setup.inst_id,
                limit=500,
            )

            setup = apply_order_flow(
                setup,
                order_flow,
            )

        except Exception as error:
            setup.warnings.append(
                f"Ошибка Order Flow: {error}"
            )

        if setup.score >= MIN_FINAL_SCORE:
            save_setup(setup)
            final_setups.append(setup)

        if progress_callback:
            await progress_callback(
                "order_flow",
                index,
                len(order_flow_targets),
            )

        await asyncio.sleep(0.35)

    final_setups.sort(
        key=lambda item: (
            item.score,
            item.risk_reward,
        ),
        reverse=True,
    )

    return final_setups, len(selected)


def price_text(value: float) -> str:
    if value >= 10_000:
        return f"{value:.1f}"

    if value >= 100:
        return f"{value:.2f}"

    if value >= 1:
        return f"{value:.4f}"

    if value >= 0.01:
        return f"{value:.6f}"

    return f"{value:.9f}"


def trend_text(trend: str) -> str:
    names = {
        "strong_bullish": "сильный восходящий",
        "bullish": "восходящий",
        "neutral": "нейтральный",
        "bearish": "нисходящий",
        "strong_bearish": "сильный нисходящий",
    }

    return names.get(trend, trend)


def order_flow_text(direction: str) -> str:
    names = {
        "bullish": "покупатели сильнее",
        "bearish": "продавцы сильнее",
        "neutral": "баланс сторон",
        "not_checked": "не проверен",
    }

    return names.get(direction, direction)


def format_setup(setup: Setup) -> str:
    strength = (
        "СИЛЬНЫЙ СЕТАП"
        if setup.score >= STRONG_SETUP_SCORE
        else "ПОТЕНЦИАЛЬНЫЙ СЕТАП"
    )

    reasons = "\n".join(
        f"• {reason}"
        for reason in setup.reasons[:16]
    )

    warnings_text = ""

    if setup.warnings:
        warnings = "\n".join(
            f"• {warning}"
            for warning in setup.warnings[:6]
        )

        warnings_text = (
            f"\n\n⚠️ Риски:\n{warnings}"
        )

    return (
        f"🔎 {strength}\n\n"
        f"Инструмент: {setup.inst_id}\n"
        f"Направление: {setup.direction}\n"
        f"Итоговая оценка: {setup.score}/100\n"
        f"RR до TP2: 1:{setup.risk_reward:.2f}\n\n"

        f"🎯 Вход: "
        f"{price_text(setup.entry_low)} – "
        f"{price_text(setup.entry_high)}\n"
        f"🛑 Стоп: "
        f"{price_text(setup.stop_loss)}\n"
        f"✅ TP1: "
        f"{price_text(setup.take_profit_1)}\n"
        f"✅ TP2: "
        f"{price_text(setup.take_profit_2)}\n"
        f"✅ TP3: "
        f"{price_text(setup.take_profit_3)}\n\n"

        f"📊 Технические данные\n"
        f"RSI 15m: {setup.rsi_15m:.1f}\n"
        f"RSI 1H: {setup.rsi_1h:.1f}\n"
        f"RSI 4H: {setup.rsi_4h:.1f}\n"
        f"Funding: "
        f"{setup.funding_rate * 100:.4f}%\n"
        f"Open Interest: "
        f"{setup.open_interest:,.2f}\n"
        f"Спред: "
        f"{setup.spread_percent:.4f}%\n"
        f"Объём свечи: "
        f"x{setup.volume_ratio:.2f}\n"
        f"Стакан покупателей: "
        f"{setup.order_book_imbalance * 100:.1f}%\n\n"

        f"📈 Тренды\n"
        f"15m: {trend_text(setup.trend_15m)}\n"
        f"1H: {trend_text(setup.trend_1h)}\n"
        f"4H: {trend_text(setup.trend_4h)}\n"
        f"BTC: {trend_text(setup.btc_trend)}\n\n"

        f"📊 Order Flow\n"
        f"Состояние: "
        f"{order_flow_text(setup.order_flow_direction)}\n"
        f"Оценка: "
        f"{setup.order_flow_score:+d}/100\n"
        f"Delta: "
        f"{format_money(setup.order_flow_delta)}\n"
        f"Delta %: "
        f"{setup.order_flow_delta_percent:+.2f}%\n"
        f"CVD выборки: "
        f"{format_money(setup.order_flow_cvd)}\n"
        f"Крупные покупки: "
        f"{format_money(setup.large_buy_volume)}\n"
        f"Крупные продажи: "
        f"{format_money(setup.large_sell_volume)}\n\n"

        f"Причины:\n{reasons}"
        f"{warnings_text}\n\n"

        "Order Flow рассчитан по последней "
        "доступной выборке сделок. "
        "Сигнал не гарантирует прибыль."
    )


def setup_keyboard(
    setup_id: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Опубликовать",
                    callback_data=(
                        f"assistant_publish:{setup_id}"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=(
                        f"assistant_reject:{setup_id}"
                    ),
                )
            ],
        ]
    )


@router.message(Command("marketscan"))
async def market_scan(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return

    progress_message = await message.answer(
        "🔎 Сканирование запущено\n\n"
        "Этап 1: технический анализ 80 монет.\n"
        "Этап 2: Order Flow для 20 лучших.\n\n"
        "Не запускайте команду повторно."
    )

    async def update_progress(
        stage: str,
        completed: int,
        total: int,
    ) -> None:
        try:
            if stage == "technical":
                text = (
                    "🔎 Технический анализ\n\n"
                    f"Проверено: {completed}/{total}"
                )
            else:
                text = (
                    "📊 Анализ Order Flow\n\n"
                    f"Проверено: {completed}/{total}"
                )

            await progress_message.edit_text(
                text
            )
        except Exception:
            pass

    try:
        setups, analysed_count = (
            await scan_markets(
                progress_callback=update_progress
            )
        )

    except Exception as error:
        await progress_message.edit_text(
            "❌ Ошибка сканирования:\n\n"
            f"{error}"
        )
        return

    if not setups:
        await progress_message.edit_text(
            "✅ Сканирование завершено\n\n"
            f"Технически проверено: "
            f"{analysed_count}\n"
            "Сетапов, подтверждённых "
            "Order Flow, сейчас нет."
        )
        return

    await progress_message.edit_text(
        "✅ Сканирование завершено\n\n"
        f"Технически проверено: "
        f"{analysed_count}\n"
        f"Качественных сетапов: "
        f"{len(setups)}\n"
        f"Показываю лучшие: "
        f"{min(len(setups), MAX_RESULTS_TO_SHOW)}"
    )

    for setup in setups[
        :MAX_RESULTS_TO_SHOW
    ]:
        await message.answer(
            format_setup(setup),
            reply_markup=setup_keyboard(
                setup.setup_id
            ),
        )


@router.callback_query(
    F.data.startswith("assistant_publish:")
)
async def publish_setup(
    callback: CallbackQuery,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    setup_id = callback.data.split(
        ":",
        maxsplit=1,
    )[1]

    setup = get_saved_setup(setup_id)

    if setup is None:
        await callback.answer(
            "Сетап не найден.",
            show_alert=True,
        )
        return

    if has_active_signal(setup.inst_id):
        await callback.answer(
            "По монете уже есть активный сигнал.",
            show_alert=True,
        )
        return

    symbol = setup.inst_id.replace(
        "-USDT-SWAP",
        "/USDT",
    )

    signal_id = create_signal(
        symbol=symbol,
        direction=setup.direction,
        entry=(
            f"{price_text(setup.entry_low)}-"
            f"{price_text(setup.entry_high)}"
        ),
        stop_loss=price_text(
            setup.stop_loss
        ),
        take_profit_1=price_text(
            setup.take_profit_1
        ),
        take_profit_2=price_text(
            setup.take_profit_2
        ),
        take_profit_3=price_text(
            setup.take_profit_3
        ),
        risk="Не более 0.5–1%",
        comment=(
            "Сетап подтверждён техническим анализом, "
            "Order Flow и затем одобрен администратором."
        ),
        score=setup.score,
        confirmations=setup.reasons,
        warnings=setup.warnings,
    )

    signal = get_signal(signal_id)

    if signal is None:
        await callback.answer(
            "Не удалось создать сигнал.",
            show_alert=True,
        )
        return

    await callback.answer(
        "Публикую сигнал..."
    )

    result = await send_signal_to_users(
        callback.bot,
        signal,
    )

    update_setup_status(
        setup_id,
        "published",
    )

    await callback.message.edit_text(
        "✅ Сетап опубликован\n\n"
        f"{format_signal(signal)}"
    )

    await callback.message.answer(
        "📤 Результат рассылки\n\n"
        f"VIP: {result['vip_sent']}\n"
        f"Бесплатных: "
        f"{result['free_sent']}\n"
        f"Лимит закончился: "
        f"{result['limit_exhausted']}\n"
        f"Ошибок: {result['failed']}"
    )


@router.callback_query(
    F.data.startswith("assistant_reject:")
)
async def reject_setup(
    callback: CallbackQuery,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    setup_id = callback.data.split(
        ":",
        maxsplit=1,
    )[1]

    update_setup_status(
        setup_id,
        "rejected",
    )

    await callback.message.edit_text(
        "❌ Сетап отклонён."
    )

    await callback.answer()


create_assistant_tables()