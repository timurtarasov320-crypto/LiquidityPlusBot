import asyncio
from dataclasses import dataclass
from typing import Any, Optional

import aiohttp


OKX_API_URL = "https://www.okx.com"

DEFAULT_TRADE_LIMIT = 500
MAX_CONCURRENCY = 4


@dataclass
class OrderFlowResult:
    inst_id: str

    total_trades: int
    total_volume: float

    buy_volume: float
    sell_volume: float

    buy_trades: int
    sell_trades: int

    delta: float
    delta_percent: float
    cvd: float

    average_trade_size: float

    large_buy_volume: float
    large_sell_volume: float
    large_trade_threshold: float

    buy_ratio: float
    sell_ratio: float

    score: int
    direction: str

    reasons: list[str]
    warnings: list[str]


class OrderFlowClient:
    def __init__(self) -> None:
        self.timeout = aiohttp.ClientTimeout(total=20)
        self.semaphore = asyncio.Semaphore(
            MAX_CONCURRENCY
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
        retries: int = 3,
    ) -> list[dict[str, Any]]:
        if self.session is None:
            raise RuntimeError(
                "HTTP-сессия Order Flow не создана."
            )

        url = f"{OKX_API_URL}{endpoint}"

        for attempt in range(retries):
            async with self.semaphore:
                try:
                    async with self.session.get(
                        url,
                        params=params or {},
                    ) as response:
                        response.raise_for_status()
                        payload = await response.json()

                    if str(payload.get("code")) == "0":
                        return payload.get("data", [])

                    error_message = payload.get(
                        "msg",
                        "Неизвестная ошибка OKX",
                    )

                    raise RuntimeError(
                        f"OKX API: {error_message}"
                    )

                except (
                    aiohttp.ClientError,
                    asyncio.TimeoutError,
                    RuntimeError,
                ) as error:
                    if attempt >= retries - 1:
                        raise RuntimeError(
                            "Не удалось получить сделки "
                            f"для Order Flow: {error}"
                        ) from error

                    await asyncio.sleep(
                        1.5 * (attempt + 1)
                    )

        return []


async def get_recent_trades(
    client: OrderFlowClient,
    inst_id: str,
    limit: int = DEFAULT_TRADE_LIMIT,
) -> list[dict[str, Any]]:
    safe_limit = max(
        1,
        min(int(limit), DEFAULT_TRADE_LIMIT),
    )

    return await client.get(
        "/api/v5/market/trades",
        {
            "instId": inst_id,
            "limit": str(safe_limit),
        },
    )


def parse_trades(
    trades: list[dict[str, Any]],
) -> list[dict[str, float | str | int]]:
    parsed: list[
        dict[str, float | str | int]
    ] = []

    for trade in trades:
        try:
            price = float(
                trade.get("px") or 0
            )

            size = float(
                trade.get("sz") or 0
            )

            side = str(
                trade.get("side") or ""
            ).lower()

            timestamp = int(
                trade.get("ts") or 0
            )

            trade_id = str(
                trade.get("tradeId") or ""
            )

        except (
            TypeError,
            ValueError,
        ):
            continue

        if (
            price <= 0
            or size <= 0
            or side not in ("buy", "sell")
        ):
            continue

        notional = price * size

        parsed.append(
            {
                "price": price,
                "size": size,
                "notional": notional,
                "side": side,
                "timestamp": timestamp,
                "trade_id": trade_id,
            }
        )

    parsed.sort(
        key=lambda item: int(
            item["timestamp"]
        )
    )

    return parsed


def percentile(
    values: list[float],
    percentile_value: float,
) -> float:
    if not values:
        return 0.0

    sorted_values = sorted(values)

    index = (
        len(sorted_values) - 1
    ) * percentile_value

    lower_index = int(index)
    upper_index = min(
        lower_index + 1,
        len(sorted_values) - 1,
    )

    weight = index - lower_index

    return (
        sorted_values[lower_index]
        * (1 - weight)
        + sorted_values[upper_index]
        * weight
    )


def calculate_order_flow(
    inst_id: str,
    trades: list[dict[str, Any]],
) -> Optional[OrderFlowResult]:
    parsed = parse_trades(trades)

    if len(parsed) < 20:
        return None

    buy_volume = 0.0
    sell_volume = 0.0

    buy_trades = 0
    sell_trades = 0

    total_volume = 0.0
    cvd = 0.0

    notionals: list[float] = []

    for trade in parsed:
        notional = float(
            trade["notional"]
        )

        total_volume += notional
        notionals.append(notional)

        if trade["side"] == "buy":
            buy_volume += notional
            buy_trades += 1
            cvd += notional
        else:
            sell_volume += notional
            sell_trades += 1
            cvd -= notional

    total_trades = len(parsed)

    delta = buy_volume - sell_volume

    delta_percent = (
        delta / total_volume * 100
        if total_volume > 0
        else 0.0
    )

    average_trade_size = (
        total_volume / total_trades
        if total_trades > 0
        else 0.0
    )

    large_trade_threshold = percentile(
        notionals,
        0.90,
    )

    large_buy_volume = 0.0
    large_sell_volume = 0.0

    for trade in parsed:
        notional = float(
            trade["notional"]
        )

        if notional < large_trade_threshold:
            continue

        if trade["side"] == "buy":
            large_buy_volume += notional
        else:
            large_sell_volume += notional

    buy_ratio = (
        buy_volume / total_volume
        if total_volume > 0
        else 0.5
    )

    sell_ratio = (
        sell_volume / total_volume
        if total_volume > 0
        else 0.5
    )

    score = 0
    reasons: list[str] = []
    warnings: list[str] = []

    if delta_percent >= 20:
        score += 35
        reasons.append(
            "Сильный перевес агрессивных покупок"
        )
    elif delta_percent >= 10:
        score += 22
        reasons.append(
            "Умеренный перевес агрессивных покупок"
        )
    elif delta_percent >= 4:
        score += 10
        reasons.append(
            "Небольшой перевес покупателей"
        )

    if delta_percent <= -20:
        score -= 35
        reasons.append(
            "Сильный перевес агрессивных продаж"
        )
    elif delta_percent <= -10:
        score -= 22
        reasons.append(
            "Умеренный перевес агрессивных продаж"
        )
    elif delta_percent <= -4:
        score -= 10
        reasons.append(
            "Небольшой перевес продавцов"
        )

    large_delta = (
        large_buy_volume
        - large_sell_volume
    )

    large_total = (
        large_buy_volume
        + large_sell_volume
    )

    large_delta_percent = (
        large_delta / large_total * 100
        if large_total > 0
        else 0.0
    )

    if large_delta_percent >= 20:
        score += 30
        reasons.append(
            "Крупные сделки преобладают на покупку"
        )
    elif large_delta_percent >= 8:
        score += 15
        reasons.append(
            "Крупные покупатели активнее продавцов"
        )

    if large_delta_percent <= -20:
        score -= 30
        reasons.append(
            "Крупные сделки преобладают на продажу"
        )
    elif large_delta_percent <= -8:
        score -= 15
        reasons.append(
            "Крупные продавцы активнее покупателей"
        )

    trade_count_delta = (
        buy_trades - sell_trades
    )

    trade_count_total = (
        buy_trades + sell_trades
    )

    trade_count_delta_percent = (
        trade_count_delta
        / trade_count_total
        * 100
        if trade_count_total > 0
        else 0.0
    )

    if trade_count_delta_percent >= 12:
        score += 12
        reasons.append(
            "Количество покупок выше количества продаж"
        )

    if trade_count_delta_percent <= -12:
        score -= 12
        reasons.append(
            "Количество продаж выше количества покупок"
        )

    if abs(delta_percent) < 3:
        warnings.append(
            "Выраженного перевеса сторон нет"
        )

    if total_trades < 100:
        warnings.append(
            "Мало сделок для устойчивой оценки"
        )

    score = max(
        -100,
        min(score, 100),
    )

    if score >= 25:
        direction = "bullish"
    elif score <= -25:
        direction = "bearish"
    else:
        direction = "neutral"

    return OrderFlowResult(
        inst_id=inst_id,
        total_trades=total_trades,
        total_volume=total_volume,
        buy_volume=buy_volume,
        sell_volume=sell_volume,
        buy_trades=buy_trades,
        sell_trades=sell_trades,
        delta=delta,
        delta_percent=delta_percent,
        cvd=cvd,
        average_trade_size=average_trade_size,
        large_buy_volume=large_buy_volume,
        large_sell_volume=large_sell_volume,
        large_trade_threshold=large_trade_threshold,
        buy_ratio=buy_ratio,
        sell_ratio=sell_ratio,
        score=score,
        direction=direction,
        reasons=reasons,
        warnings=warnings,
    )


async def analyse_order_flow(
    inst_id: str,
    limit: int = DEFAULT_TRADE_LIMIT,
) -> Optional[OrderFlowResult]:
    async with OrderFlowClient() as client:
        trades = await get_recent_trades(
            client=client,
            inst_id=inst_id,
            limit=limit,
        )

    return calculate_order_flow(
        inst_id=inst_id,
        trades=trades,
    )


def format_money(value: float) -> str:
    absolute = abs(value)

    if absolute >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"

    if absolute >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"

    if absolute >= 1_000:
        return f"${value / 1_000:.2f}K"

    return f"${value:.2f}"


def format_order_flow(
    result: OrderFlowResult,
) -> str:
    direction_names = {
        "bullish": "🟢 Покупатели сильнее",
        "bearish": "🔴 Продавцы сильнее",
        "neutral": "⚪ Баланс сторон",
    }

    reasons_text = (
        "\n".join(
            f"• {reason}"
            for reason in result.reasons
        )
        if result.reasons
        else "• Явных подтверждений нет"
    )

    warnings_text = ""

    if result.warnings:
        warnings_text = (
            "\n\n⚠️ Ограничения:\n"
            + "\n".join(
                f"• {warning}"
                for warning in result.warnings
            )
        )

    return (
        "📊 ORDER FLOW\n\n"
        f"Инструмент: {result.inst_id}\n"
        f"Состояние: "
        f"{direction_names[result.direction]}\n"
        f"Оценка: {result.score:+d}/100\n\n"
        f"Сделок: {result.total_trades}\n"
        f"Объём выборки: "
        f"{format_money(result.total_volume)}\n\n"
        f"Агрессивные покупки: "
        f"{format_money(result.buy_volume)}\n"
        f"Агрессивные продажи: "
        f"{format_money(result.sell_volume)}\n\n"
        f"Delta: {format_money(result.delta)}\n"
        f"Delta %: {result.delta_percent:+.2f}%\n"
        f"CVD выборки: {format_money(result.cvd)}\n\n"
        f"Крупные покупки: "
        f"{format_money(result.large_buy_volume)}\n"
        f"Крупные продажи: "
        f"{format_money(result.large_sell_volume)}\n"
        f"Порог крупной сделки: "
        f"{format_money(result.large_trade_threshold)}\n\n"
        f"Причины:\n{reasons_text}"
        f"{warnings_text}\n\n"
        "CVD рассчитан только по доступной "
        "выборке последних сделок."
    )