# APEX SIGNAL AI — Complete Signal-Only Build

APEX SIGNAL AI is a FastAPI web app with a dark animated dashboard, dynamic TradoWix instrument lists, a candle chart, and a technical-analysis signal endpoint. It does **not** place trades.

## Included

- Live and OTC tabs are populated from the authenticated TradoWix instrument feed; no fixed pair list is hardcoded.
- Search filters the loaded pairs by symbol/name. Open/closed and active/inactive status is shown; closed/inactive pairs remain visible but are disabled for signal selection.
- Animated pair-selection highlight.
- 30-second and 1-minute historical candles, requesting up to 1,000 bars where the provider makes that history available.
- Live tick subscription and in-browser candle updates.
- Signal engine based on returned OHLC candles: EMA 9/21/50, RSI 14, MACD histogram, Bollinger Bands, ATR, recent support/resistance breakout, and candle momentum.
- CALL/PUT output only when enough real candles are received. If the feed is disconnected, pair is closed, or fewer than 60 candles are available, the API returns an error instead of fabricating a signal.
- Signal confidence is a heuristic rule-strength score, **not** a verified probability or win rate.
- Credentials are read only from server environment variables. No credentials are accepted by the browser and no trade execution route is included.

## Deploy

Use Python 3.11+ and a Python web-service host that supports outbound WebSockets.

- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn app:app --host 0.0.0.0 --port $PORT`
- Set private environment variables on the host:
  - `TRADOWIX_EMAIL` = TradoWix account email
  - `TRADOWIX_PASSWORD` = TradoWix account password

Do not add `.env` or real credentials to GitHub. Keep the repository private where possible. `.env.example` is only a blank template.

After deployment, open the app, open **Chart & API Diagnostics**, click **Connect TradoWix API**, choose Live or OTC, search/select a pair, and load the chart. Then return to the signal panel and generate a signal.

## Endpoints

- `GET /api/health`
- `POST /api/connect` — authenticates using private host environment variables
- `GET /api/status`
- `GET /api/assets?market=live|otc|all`
- `GET /api/chart?symbol=...&timeframe=30|60&limit=1000`
- `GET /api/signal?symbol=...&timeframe=30|60`
- `GET /api/ticks/{symbol}`
- `POST /api/disconnect`

## Important integration limits

This package has been compile-checked and smoke-tested with the backend disconnected, and the analysis engine has been tested with synthetic OHLC data. The actual TradoWix account login, current instrument feed, broker candle history, and live tick stream cannot be verified in this build environment. Their availability depends on the account, broker service, SDK compatibility, and deployment host's outbound WebSocket support. The app reports provider errors rather than substituting fake prices or signals.

The included PyTradoWix library is an unofficial SDK copy. Confirm its current compatibility with TradoWix before relying on the service.
