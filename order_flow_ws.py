import asyncio
import json
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

import aiohttp


OKX_PUBLIC_WS_URL = "wss://ws.okx.com:8443/ws/v5/public"

WINDOW_SECONDS = 15 * 60
MAX_TRADES_PER_MARKET = 20_000
PING_INTERVAL_SECONDS = 20
RECONNECT_DELAY_SECONDS = 5


@dataclass
class TradeRecord:
    timestamp_ms: int
    side: str
    price: float
    size: float
    notional: float


@dataclass
class LiveOrderFlow:
    inst_id: str
    buy_volume: float = 0.0
    sell_volume: float = 0.0
    buy_trades: int = 0
    sell_trades: int = 0
    cvd: float = 0.0
    last_price: float = 0.0
    updated_at: float = 0.0
    trades: deque[TradeRecord] = field(
        default_factory=lambda: deque(
            maxlen=MAX_TRADES_PER_MARKET
        )
    )


class OKXOrderFlowWebSocket:
    def __init__(self) -> None:
        self.markets: set[str] = set()
        self.data: dict[str, LiveOrderFlow] = {}
        self.task: Optional[asyncio.Task] = None
        self.running = False
        self.lock = asyncio.Lock()

    async def start(
        self,
        instruments: list[str],
    ) -> None:
        async with self.lock:
            self.markets = set(instruments)

            for inst_id in instruments:
                self.data.setdefault(
                    inst_id,
                    LiveOrderFlow(inst_id=inst_id),
                )

            if self.task and not self.task.done():
                await self.update_subscriptions(
                    instruments
                )
                return

            self.running = True
            self.task = asyncio.create_task(
                self._run()
            )

    async def stop(self) -> None:
        self.running = False

        if self.task:
            self.task.cancel()

            try:
                await self.task
            except asyncio.CancelledError:
                pass

            self.task = None

    async def update_subscriptions(
        self,
        instruments: list[str],
    ) -> None:
        async with self.lock:
            self.markets = set(instruments)

            for inst_id in instruments:
                self.data.setdefault(
                    inst_id,
                    LiveOrderFlow(inst_id=inst_id),
                )

    async def _run(self) -> None:
        while self.running:
            try:
                await self._connect_and_listen()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                print(
                    "Ошибка OKX WebSocket Order Flow:",
                    error,
                )

                await asyncio.sleep(
                    RECONNECT_DELAY_SECONDS
                )

    async def _connect_and_listen(self) -> None:
        timeout = aiohttp.ClientTimeout(
            total=None,
            sock_connect=20,
            sock_read=None,
        )

        async with aiohttp.ClientSession(
            timeout=timeout
        ) as session:
            async with session.ws_connect(
                OKX_PUBLIC_WS_URL,
                heartbeat=PING_INTERVAL_SECONDS,
                autoping=True,
            ) as websocket:
                await self._subscribe(websocket)

                print(
                    "OKX Order Flow WebSocket подключён"
                )

                async for message in websocket:
                    if not self.running:
                        break

                    if message.type == aiohttp.WSMsgType.TEXT:
                        await self._handle_message(
                            message.data
                        )

                    elif message.type in (
                        aiohttp.WSMsgType.CLOSED,
                        aiohttp.WSMsgType.ERROR,
                    ):
                        break

    async def _subscribe(
        self,
        websocket: aiohttp.ClientWebSocketResponse,
    ) -> None:
        args = [
            {
                "channel": "trades",
                "instId": inst_id,
            }
            for inst_id in sorted(self.markets)
        ]

        if not args:
            return

        await websocket.send_json(
            {
                "op": "subscribe",
                "args": args,
            }
        )

    async def _handle_message(
        self,
        raw_message: str,
    ) -> None:
        try:
            payload = json.loads(raw_message)
        except json.JSONDecodeError:
            return

        if payload.get("event"):
            if payload.get("event") == "error":
                print(
                    "Ошибка подписки OKX:",
                    payload,
                )
            return

        argument = payload.get("arg", {})
        channel = argument.get("channel")
        inst_id = argument.get("instId")

        if channel != "trades" or not inst_id:
            return

        trades = payload.get("data", [])

        for trade in trades:
            self._process_trade(
                inst_id,
                trade,
            )

    def _process_trade(
        self,
        inst_id: str,
        trade: dict,
    ) -> None:
        try:
            price = float(trade.get("px") or 0)
            size = float(trade.get("sz") or 0)
            side = str(
                trade.get("side") or ""
            ).lower()
            timestamp_ms = int(
                trade.get("ts") or 0
            )
        except (TypeError, ValueError):
            return

        if (
            price <= 0
            or size <= 0
            or side not in ("buy", "sell")
        ):
            return

        state = self.data.setdefault(
            inst_id,
            LiveOrderFlow(inst_id=inst_id),
        )

        notional = price * size

        record = TradeRecord(
            timestamp_ms=timestamp_ms,
            side=side,
            price=price,
            size=size,
            notional=notional,
        )

        state.trades.append(record)
        state.last_price = price
        state.updated_at = time.time()

        if side == "buy":
            state.buy_volume += notional
            state.buy_trades += 1
            state.cvd += notional
        else:
            state.sell_volume += notional
            state.sell_trades += 1
            state.cvd -= notional

        self._remove_old_trades(state)

    def _remove_old_trades(
        self,
        state: LiveOrderFlow,
    ) -> None:
        cutoff_ms = int(
            (time.time() - WINDOW_SECONDS) * 1000
        )

        while (
            state.trades
            and state.trades[0].timestamp_ms
            < cutoff_ms
        ):
            trade = state.trades.popleft()

            if trade.side == "buy":
                state.buy_volume -= trade.notional
                state.buy_trades -= 1
                state.cvd -= trade.notional
            else:
                state.sell_volume -= trade.notional
                state.sell_trades -= 1
                state.cvd += trade.notional

        state.buy_volume = max(
            0.0,
            state.buy_volume,
        )
        state.sell_volume = max(
            0.0,
            state.sell_volume,
        )
        state.buy_trades = max(
            0,
            state.buy_trades,
        )
        state.sell_trades = max(
            0,
            state.sell_trades,
        )

    def get_snapshot(
        self,
        inst_id: str,
    ) -> Optional[dict]:
        state = self.data.get(inst_id)

        if state is None:
            return None

        self._remove_old_trades(state)

        total_volume = (
            state.buy_volume
            + state.sell_volume
        )

        delta = (
            state.buy_volume
            - state.sell_volume
        )

        delta_percent = (
            delta / total_volume * 100
            if total_volume > 0
            else 0.0
        )

        total_trades = (
            state.buy_trades
            + state.sell_trades
        )

        if delta_percent >= 15:
            direction = "bullish"
        elif delta_percent <= -15:
            direction = "bearish"
        else:
            direction = "neutral"

        score = int(
            max(
                -100,
                min(
                    100,
                    delta_percent * 2.5,
                ),
            )
        )

        return {
            "inst_id": inst_id,
            "window_minutes": (
                WINDOW_SECONDS // 60
            ),
            "total_trades": total_trades,
            "buy_trades": state.buy_trades,
            "sell_trades": state.sell_trades,
            "buy_volume": state.buy_volume,
            "sell_volume": state.sell_volume,
            "total_volume": total_volume,
            "delta": delta,
            "delta_percent": delta_percent,
            "cvd": state.cvd,
            "last_price": state.last_price,
            "direction": direction,
            "score": score,
            "updated_at": state.updated_at,
        }

    def get_all_snapshots(self) -> list[dict]:
        snapshots = []

        for inst_id in sorted(self.markets):
            snapshot = self.get_snapshot(
                inst_id
            )

            if snapshot:
                snapshots.append(snapshot)

        return snapshots


order_flow_ws = OKXOrderFlowWebSocket()