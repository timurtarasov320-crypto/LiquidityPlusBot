import asyncio
import html
import os
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import aiohttp
from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

router = Router()

COINGECKO_BASE_URL = os.getenv(
    "COINGECKO_BASE_URL",
    "https://api.coingecko.com/api/v3",
).rstrip("/")
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY", "").strip()
FEAR_GREED_URL = os.getenv(
    "FEAR_GREED_URL",
    "https://api.alternative.me/fng/?limit=1&format=json",
)
NEWS_RSS_URLS = [
    url.strip()
    for url in os.getenv(
        "NEWS_RSS_URLS",
        "https://www.coindesk.com/arc/outboundfeeds/rss/;"
        "https://cointelegraph.com/rss",
    ).split(";")
    if url.strip()
]
HTTP_TIMEOUT_SECONDS = int(os.getenv("MARKET_HTTP_TIMEOUT", "12"))
CACHE_TTL_SECONDS = int(os.getenv("MARKET_CACHE_TTL", "90"))

_CACHE: dict[str, tuple[float, Any]] = {}


@dataclass(slots=True)
class NewsItem:
    title: str
    url: str
    source: str
    published: str


def _cache_get(key: str) -> Any | None:
    item = _CACHE.get(key)
    if not item:
        return None
    created_at, value = item
    if time.monotonic() - created_at > CACHE_TTL_SECONDS:
        _CACHE.pop(key, None)
        return None
    return value


def _cache_set(key: str, value: Any) -> Any:
    _CACHE[key] = (time.monotonic(), value)
    return value


def _headers() -> dict[str, str]:
    result = {
        "Accept": "application/json",
        "User-Agent": "LiquidityPlusBot/2.0",
    }
    if COINGECKO_API_KEY:
        result["x-cg-demo-api-key"] = COINGECKO_API_KEY
    return result


async def _get_json(url: str, params: dict[str, Any] | None = None) -> Any:
    timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout, headers=_headers()) as session:
        async with session.get(url, params=params) as response:
            response.raise_for_status()
            return await response.json(content_type=None)


async def _get_text(url: str) -> str:
    timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS)
    headers = {
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
        "User-Agent": "LiquidityPlusBot/2.0",
    }
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        async with session.get(url) as response:
            response.raise_for_status()
            return await response.text(errors="ignore")


def _money(value: float | int | None) -> str:
    number = float(value or 0)
    if abs(number) >= 1_000_000_000_000:
        return f"${number / 1_000_000_000_000:.2f}T"
    if abs(number) >= 1_000_000_000:
        return f"${number / 1_000_000_000:.2f}B"
    if abs(number) >= 1_000_000:
        return f"${number / 1_000_000:.2f}M"
    if abs(number) >= 1_000:
        return f"${number / 1_000:.2f}K"
    return f"${number:,.2f}"


def _percent(value: float | int | None) -> str:
    number = float(value or 0)
    icon = "🟢" if number > 0 else "🔴" if number < 0 else "⚪"
    return f"{icon} {number:+.2f}%"


def _escape(value: Any) -> str:
    return html.escape(str(value or ""), quote=False)


def market_hub_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🌍 Обзор рынка", callback_data="hub_overview"),
                InlineKeyboardButton(text="🔥 Топ монет", callback_data="hub_movers"),
            ],
            [
                InlineKeyboardButton(text="📡 Подтверждения", callback_data="hub_confirmations"),
                InlineKeyboardButton(text="😨 Настроение", callback_data="hub_sentiment"),
            ],
            [
                InlineKeyboardButton(text="📰 AI Новости", callback_data="hub_news"),
                InlineKeyboardButton(text="🔄 Обновить", callback_data="menu_market_hub"),
            ],
            [
                InlineKeyboardButton(text="⬅️ Главное меню", callback_data="menu_home"),
            ],
        ]
    )


def back_keyboard(refresh_callback: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data=refresh_callback)],
            [InlineKeyboardButton(text="⬅️ Trading Hub", callback_data="menu_market_hub")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu_home")],
        ]
    )


async def _render(callback: CallbackQuery, text: str, markup: InlineKeyboardMarkup) -> None:
    try:
        if callback.message.photo:
            await callback.message.edit_caption(
                caption=text,
                reply_markup=markup,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        else:
            await callback.message.edit_text(
                text=text,
                reply_markup=markup,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
    except Exception as error:
        if "message is not modified" not in str(error).lower():
            await callback.message.answer(
                text,
                reply_markup=markup,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )


async def get_global_market() -> dict[str, Any]:
    cached = _cache_get("global")
    if cached is not None:
        return cached
    payload = await _get_json(f"{COINGECKO_BASE_URL}/global")
    return _cache_set("global", payload.get("data", {}))


async def get_markets() -> list[dict[str, Any]]:
    cached = _cache_get("markets")
    if cached is not None:
        return cached
    payload = await _get_json(
        f"{COINGECKO_BASE_URL}/coins/markets",
        params={
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": 100,
            "page": 1,
            "sparkline": "false",
            "price_change_percentage": "24h",
        },
    )
    return _cache_set("markets", payload)


async def get_fear_greed() -> dict[str, Any]:
    cached = _cache_get("fear_greed")
    if cached is not None:
        return cached
    payload = await _get_json(FEAR_GREED_URL)
    data = (payload.get("data") or [{}])[0]
    return _cache_set("fear_greed", data)


def _rss_items(xml_text: str, source: str) -> list[NewsItem]:
    root = ET.fromstring(xml_text)
    items: list[NewsItem] = []
    for node in root.findall(".//item")[:8]:
        title = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        published = (
            node.findtext("pubDate")
            or node.findtext("{http://purl.org/dc/elements/1.1/}date")
            or ""
        ).strip()
        if title and link:
            items.append(NewsItem(title=title, url=link, source=source, published=published))
    return items


async def get_news() -> list[NewsItem]:
    cached = _cache_get("news")
    if cached is not None:
        return cached

    async def load(url: str) -> list[NewsItem]:
        try:
            text = await _get_text(url)
            host = url.split("//", 1)[-1].split("/", 1)[0].replace("www.", "")
            return _rss_items(text, host)
        except Exception as error:
            print(f"News RSS error {url}: {error}")
            return []

    groups = await asyncio.gather(*(load(url) for url in NEWS_RSS_URLS))
    result: list[NewsItem] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            key = item.title.casefold()
            if key not in seen:
                seen.add(key)
                result.append(item)
    return _cache_set("news", result[:10])


@router.callback_query(F.data == "menu_market_hub")
async def market_hub(callback: CallbackQuery):
    text = (
        "<b>⚡ LIQUIDITYPLUS TRADING HUB</b>\n\n"
        "Рыночная информация в одном разделе:\n"
        "• глобальная капитализация и доминация;\n"
        "• лидеры роста и падения;\n"
        "• индекс страха и жадности;\n"
        "• свежие криптоновости.\n\n"
        "<i>Данные служат для анализа и не являются финансовой рекомендацией.</i>"
    )
    await _render(callback, text, market_hub_keyboard())
    await callback.answer()


@router.callback_query(F.data == "hub_overview")
async def market_overview(callback: CallbackQuery):
    await callback.answer("Обновляю данные…")
    try:
        data, markets = await asyncio.gather(get_global_market(), get_markets())
        dominance = data.get("market_cap_percentage", {})
        cap = data.get("total_market_cap", {}).get("usd")
        volume = data.get("total_volume", {}).get("usd")
        change = data.get("market_cap_change_percentage_24h_usd")
        active = data.get("active_cryptocurrencies")
        btc = next((coin for coin in markets if coin.get("id") == "bitcoin"), {})
        eth = next((coin for coin in markets if coin.get("id") == "ethereum"), {})
        text = (
            "<b>🌍 ОБЗОР КРИПТОРЫНКА</b>\n\n"
            f"Капитализация: <b>{_money(cap)}</b>\n"
            f"Изменение 24ч: <b>{_percent(change)}</b>\n"
            f"Объём 24ч: <b>{_money(volume)}</b>\n"
            f"Активных монет: <b>{int(active or 0):,}</b>\n\n"
            f"BTC dominance: <b>{float(dominance.get('btc', 0)):.2f}%</b>\n"
            f"ETH dominance: <b>{float(dominance.get('eth', 0)):.2f}%</b>\n\n"
            f"BTC: <b>{_money(btc.get('current_price'))}</b> · {_percent(btc.get('price_change_percentage_24h'))}\n"
            f"ETH: <b>{_money(eth.get('current_price'))}</b> · {_percent(eth.get('price_change_percentage_24h'))}\n\n"
            f"Обновлено: {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
        )
    except Exception as error:
        print(f"Market overview error: {error}")
        text = (
            "<b>🌍 ОБЗОР КРИПТОРЫНКА</b>\n\n"
            "Не удалось получить данные. Проверьте интернет, лимиты CoinGecko "
            "или добавьте <code>COINGECKO_API_KEY</code> в переменные окружения."
        )
    await _render(callback, text, back_keyboard("hub_overview"))


@router.callback_query(F.data == "hub_movers")
async def market_movers(callback: CallbackQuery):
    await callback.answer("Ищу лидеров рынка…")
    try:
        markets = await get_markets()
        eligible = [coin for coin in markets if coin.get("price_change_percentage_24h") is not None]
        gainers = sorted(eligible, key=lambda x: x["price_change_percentage_24h"], reverse=True)[:5]
        losers = sorted(eligible, key=lambda x: x["price_change_percentage_24h"])[:5]
        volume = sorted(eligible, key=lambda x: float(x.get("total_volume") or 0), reverse=True)[:5]

        def lines(items: list[dict[str, Any]], include_volume: bool = False) -> str:
            result = []
            for index, coin in enumerate(items, 1):
                symbol = _escape(str(coin.get("symbol", "")).upper())
                if include_volume:
                    value = _money(coin.get("total_volume"))
                else:
                    value = _percent(coin.get("price_change_percentage_24h"))
                result.append(f"{index}. <b>{symbol}</b> — {value}")
            return "\n".join(result)

        text = (
            "<b>🔥 ТОП МОНЕТ ЗА 24 ЧАСА</b>\n\n"
            "<b>Лидеры роста</b>\n"
            f"{lines(gainers)}\n\n"
            "<b>Лидеры падения</b>\n"
            f"{lines(losers)}\n\n"
            "<b>Максимальный объём</b>\n"
            f"{lines(volume, include_volume=True)}\n\n"
            "<i>Выборка: топ-100 монет по капитализации.</i>"
        )
    except Exception as error:
        print(f"Market movers error: {error}")
        text = "<b>🔥 ТОП МОНЕТ</b>\n\nДанные временно недоступны. Попробуйте обновить позже."
    await _render(callback, text, back_keyboard("hub_movers"))


@router.callback_query(F.data == "hub_sentiment")
async def market_sentiment(callback: CallbackQuery):
    await callback.answer("Проверяю настроение рынка…")
    try:
        data = await get_fear_greed()
        value = int(data.get("value", 0))
        label = _escape(data.get("value_classification", "Нет данных"))
        if value <= 24:
            note = "На рынке экстремальный страх. Волатильность и риск ложных движений повышены."
        elif value <= 44:
            note = "Преобладает страх. Участники рынка действуют осторожно."
        elif value <= 55:
            note = "Настроение близко к нейтральному. Нужны дополнительные подтверждения."
        elif value <= 74:
            note = "Преобладает жадность. Следите за перегретыми лонгами."
        else:
            note = "Экстремальная жадность. Риск резких фиксаций прибыли повышен."
        text = (
            "<b>😨 НАСТРОЕНИЕ РЫНКА</b>\n\n"
            f"Fear & Greed: <b>{value}/100</b>\n"
            f"Состояние: <b>{label}</b>\n\n"
            f"{note}\n\n"
            "<i>Индекс отражает настроение рынка, но не является самостоятельным сигналом на вход.</i>"
        )
    except Exception as error:
        print(f"Fear and greed error: {error}")
        text = "<b>😨 НАСТРОЕНИЕ РЫНКА</b>\n\nИндекс временно недоступен."
    await _render(callback, text, back_keyboard("hub_sentiment"))


@router.callback_query(F.data == "hub_news")
async def market_news(callback: CallbackQuery):
    await callback.answer("Загружаю новости…")
    try:
        items = await get_news()
        if not items:
            raise RuntimeError("No RSS items")
        lines = ["<b>📰 СВЕЖИЕ КРИПТОНОВОСТИ</b>", ""]
        for index, item in enumerate(items[:6], 1):
            title = _escape(item.title)
            source = _escape(item.source)
            url = html.escape(item.url, quote=True)
            impact, explanation = _news_impact(item.title)
            lines.append(
                f'{index}. <a href="{url}">{title}</a>\n'
                f'   <i>{source}</i> · <b>{impact}</b>\n'
                f'   {_escape(explanation)}'
            )
        lines.extend([
            "",
            "<i>Перед торговым решением проверяйте первоисточник и время публикации.</i>",
        ])
        text = "\n\n".join(lines)
    except Exception as error:
        print(f"News error: {error}")
        text = (
            "<b>📰 КРИПТОНОВОСТИ</b>\n\n"
            "RSS-источники временно недоступны. Их можно заменить через "
            "переменную <code>NEWS_RSS_URLS</code>, разделяя адреса точкой с запятой."
        )
    await _render(callback, text, back_keyboard("hub_news"))


OKX_PUBLIC_URL = os.getenv("OKX_PUBLIC_URL", "https://www.okx.com/api/v5").rstrip("/")
DERIVATIVE_SYMBOLS = [
    item.strip().upper()
    for item in os.getenv(
        "HUB_DERIVATIVE_SYMBOLS",
        "BTC-USDT-SWAP,ETH-USDT-SWAP,SOL-USDT-SWAP",
    ).split(",")
    if item.strip()
]


async def _okx(path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    payload = await _get_json(f"{OKX_PUBLIC_URL}/{path.lstrip('/')}", params=params)
    if str(payload.get("code", "0")) != "0":
        raise RuntimeError(payload.get("msg") or "OKX API error")
    return payload.get("data") or []


async def get_derivative_confirmations() -> list[dict[str, Any]]:
    cached = _cache_get("derivative_confirmations")
    if cached is not None:
        return cached

    async def load(inst_id: str) -> dict[str, Any]:
        funding_task = _okx("public/funding-rate", {"instId": inst_id})
        oi_task = _okx("public/open-interest", {"instType": "SWAP", "instId": inst_id})
        ratio_task = _okx(
            "rubik/stat/contracts/long-short-account-ratio",
            {"ccy": inst_id.split("-")[0], "period": "5m"},
        )
        funding, oi, ratio = await asyncio.gather(
            funding_task, oi_task, ratio_task, return_exceptions=True
        )
        funding_item = funding[0] if isinstance(funding, list) and funding else {}
        oi_item = oi[0] if isinstance(oi, list) and oi else {}
        ratio_item = ratio[0] if isinstance(ratio, list) and ratio else []
        ratio_value = 0.0
        if isinstance(ratio_item, list) and len(ratio_item) >= 2:
            ratio_value = float(ratio_item[1] or 0)
        return {
            "symbol": inst_id.replace("-USDT-SWAP", ""),
            "funding": float(funding_item.get("fundingRate") or 0) * 100,
            "oi": float(oi_item.get("oiCcy") or oi_item.get("oi") or 0),
            "long_short": ratio_value,
        }

    data = await asyncio.gather(*(load(symbol) for symbol in DERIVATIVE_SYMBOLS))
    return _cache_set("derivative_confirmations", data)


def _news_impact(title: str) -> tuple[str, str]:
    text = title.casefold()
    bullish = (
        "approval", "approved", "adoption", "partnership", "launch", "inflow",
        "surge", "record high", "upgrade", "bull", "buy", "рост", "одобр",
    )
    bearish = (
        "hack", "exploit", "lawsuit", "ban", "outflow", "liquidation", "crash",
        "fraud", "investigation", "bear", "sell-off", "взлом", "запрет", "паден",
    )
    bull = sum(word in text for word in bullish)
    bear = sum(word in text for word in bearish)
    if bull > bear:
        return "🟢 Бычье", "Новость может поддержать спрос или улучшить ожидания рынка."
    if bear > bull:
        return "🔴 Медвежье", "Новость может повысить риск, давление продавцов или волатильность."
    return "⚪ Нейтрально", "Явного направленного влияния по заголовку не обнаружено."


def _market_score(rows: list[dict[str, Any]], fear_value: int) -> tuple[int, str]:
    score = 50
    for row in rows:
        funding = float(row.get("funding") or 0)
        ratio = float(row.get("long_short") or 0)
        if -0.01 <= funding <= 0.01:
            score += 4
        elif abs(funding) >= 0.05:
            score -= 5
        if 0.85 <= ratio <= 1.25:
            score += 3
        elif ratio >= 1.8 or (ratio and ratio <= 0.55):
            score -= 4
    if 35 <= fear_value <= 65:
        score += 8
    elif fear_value <= 20 or fear_value >= 80:
        score -= 8
    score = max(0, min(100, score))
    label = "Сбалансированный" if score >= 70 else "Повышенный риск" if score < 45 else "Смешанный"
    return score, label


@router.callback_query(F.data == "hub_confirmations")
async def market_confirmations(callback: CallbackQuery):
    await callback.answer("Собираю подтверждения…")
    try:
        rows, fear = await asyncio.gather(get_derivative_confirmations(), get_fear_greed())
        fear_value = int(fear.get("value", 0) or 0)
        score, label = _market_score(rows, fear_value)
        lines = [
            "<b>📡 РЫНОЧНЫЕ ПОДТВЕРЖДЕНИЯ</b>",
            "",
            f"Market Score: <b>{score}/100</b> · <b>{label}</b>",
            f"Fear & Greed: <b>{fear_value}/100</b>",
            "",
        ]
        for row in rows:
            funding = float(row["funding"])
            ratio = float(row["long_short"])
            funding_note = "перегрев лонгов" if funding >= 0.05 else "перегрев шортов" if funding <= -0.05 else "норма"
            ratio_note = "лонги доминируют" if ratio >= 1.35 else "шорты доминируют" if ratio and ratio <= 0.75 else "баланс"
            lines.extend([
                f"<b>{_escape(row['symbol'])}</b>",
                f"Funding: <b>{funding:+.4f}%</b> · {funding_note}",
                f"Long/Short: <b>{ratio:.2f}</b> · {ratio_note}",
                f"Open Interest: <b>{row['oi']:,.2f}</b>",
                "",
            ])
        lines.append("<i>Это подтверждения контекста, а не команда на вход. Сверяйте с ценой, объёмом и ликвидностью.</i>")
        text = "\n".join(lines)
    except Exception as error:
        print(f"Confirmation hub error: {error}")
        text = "<b>📡 РЫНОЧНЫЕ ПОДТВЕРЖДЕНИЯ</b>\n\nДанные OKX временно недоступны. Попробуйте обновить позже."
    await _render(callback, text, back_keyboard("hub_confirmations"))
