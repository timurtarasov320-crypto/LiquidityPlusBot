LIQUIDITYPLUS V6 — LANGUAGES, EXCHANGES, MANUAL USDT PAYMENTS

NEW USER COMMANDS
/settings or /settings_v6 — language and preferred exchange
/trade BTCUSDT — open trading pair on OKX, Bybit, Binance or BingX
/payusdt month — create a manual USDT payment request
/paid PAYMENT_ID TXID — submit transaction hash for review

SUPPORTED LANGUAGES
Russian, Ukrainian, English. The preference is saved in v6_features.db.
V6 screens and payment notifications use the selected language. Existing legacy screens remain compatible and are not removed.

SUPPORTED EXCHANGES
OKX, Bybit, Binance Futures, BingX Perpetual.
The preferred exchange is displayed first.

MANUAL USDT PAYMENTS
1. Add to .env:
   USDT_WALLET_ADDRESS=your_wallet
   USDT_NETWORK=TRC20
2. Restart the bot.
3. User runs /payusdt month and then /paid ID TXID.
4. Owner/admin receives Approve/Reject buttons.
5. Approved payment automatically activates or extends VIP.

ADMIN COMMAND
/payments_manual — last 20 manual payment requests.

PLANS
week $5 / 7 days
month $15 / 30 days
three_months $40 / 90 days
six_months $75 / 180 days
year $145 / 365 days

DATABASE
v6_features.db is created automatically. Existing databases are preserved.
