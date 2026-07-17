LIQUIDITYPLUS TRADING HUB

Добавлено:
- глобальный обзор крипторынка;
- капитализация, объём и BTC/ETH dominance;
- цена BTC/ETH и изменение за 24 часа;
- лидеры роста и падения среди топ-100 монет;
- монеты с максимальным объёмом;
- Fear & Greed Index;
- свежие криптоновости из RSS;
- кэширование данных и обработка ошибок API.

Переменные окружения (необязательные):
COINGECKO_API_KEY=
COINGECKO_BASE_URL=https://api.coingecko.com/api/v3
FEAR_GREED_URL=https://api.alternative.me/fng/?limit=1&format=json
NEWS_RSS_URLS=https://www.coindesk.com/arc/outboundfeeds/rss/;https://cointelegraph.com/rss
MARKET_HTTP_TIMEOUT=12
MARKET_CACHE_TTL=90

Для стабильной работы CoinGecko рекомендуется создать Demo API key и добавить
его в COINGECKO_API_KEY. Без ключа публичный endpoint может работать, но лимиты
будут ниже.
