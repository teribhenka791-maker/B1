from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from signal_engine import analyze

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

client: Any = None
client_lock = asyncio.Lock()
last_quote: dict[str, dict[str, Any]] = {}
connection_info: dict[str, Any] = {
    "connected": False,
    "authenticated": False,
    "assets_loaded": False,
    "message": "Not connected. Enter credentials in the hosting environment.",
    "connected_at": None,
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    global client
    if client is not None:
        try:
            await client.close()
        except Exception:
            pass


app = FastAPI(title="APEX SIGNAL AI", version="0.2.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


class ConnectRequest(BaseModel):
    # Credentials are intentionally NOT accepted from the browser.
    confirm: bool = Field(default=True)


def _asset_dict(asset: Any) -> dict[str, Any]:
    return {
        "symbol": asset.symbol,
        "name": asset.name,
        "display_name": asset.display_name,
        "is_otc": bool(asset.is_otc),
        "is_open": bool(asset.is_open),
        "is_active": bool(asset.is_active),
        "turbo_payout_rate": asset.turbo_payout_rate,
        "blitz_payout_rate": asset.blitz_payout_rate,
    }


@app.get("/")
async def home():
    return FileResponse(ROOT / "templates" / "index.html")


@app.get("/api/health")
async def health():
    return {"ok": True, "service": "APEX SIGNAL AI", "time": int(time.time())}


@app.post("/api/connect")
async def connect_api(_: ConnectRequest):
    global client
    email = os.getenv("TRADOWIX_EMAIL", "").strip()
    password = os.getenv("TRADOWIX_PASSWORD", "")
    if not email or not password:
        raise HTTPException(
            status_code=400,
            detail="Credentials are missing. Set TRADOWIX_EMAIL and TRADOWIX_PASSWORD in private hosting Environment Variables.",
        )
    async with client_lock:
        if client is not None and connection_info.get("connected"):
            return await status_payload()
        try:
            from pytradowix import Tradowix
            from pytradowix.types import ReconnectPolicy
            candidate = Tradowix(
                email=email,
                password=password,
                is_demo=True,
                reconnect_policy=ReconnectPolicy(enabled=True, max_attempts=0),
            )
            await candidate.connect()
            # The SDK receives the instruments frame asynchronously after WS auth.
            for _ in range(20):
                if candidate.get_assets():
                    break
                await asyncio.sleep(0.25)
            client = candidate
            def quote_callback(quote: Any) -> None:
                last_quote[quote.symbol] = {
                    "symbol": quote.symbol,
                    "price": quote.price,
                    "timestamp": quote.timestamp,
                }
            client.on_quote = quote_callback
            assets = client.get_assets()
            connection_info.update({
                "connected": True,
                "authenticated": True,
                "assets_loaded": len(assets) > 0,
                "message": "Authenticated with TradoWix." if assets else "Authenticated, but instrument list is empty.",
                "connected_at": int(time.time()),
            })
            return await status_payload()
        except Exception as exc:
            client = None
            # Do not return credentials, cookies, or session tokens to the browser.
            message = str(exc)
            for secret in (email, password):
                if secret:
                    message = message.replace(secret, "[redacted]")
            connection_info.update({
                "connected": False,
                "authenticated": False,
                "assets_loaded": False,
                "message": f"Connection failed: {message[:350]}",
                "connected_at": None,
            })
            raise HTTPException(status_code=502, detail=connection_info["message"])


async def status_payload() -> dict[str, Any]:
    count = 0
    if client is not None:
        try:
            count = len(client.get_assets())
        except Exception:
            pass
    return {**connection_info, "instrument_count": count}


@app.get("/api/status")
async def api_status():
    return await status_payload()


@app.get("/api/assets")
async def assets(market: str = Query(default="live", pattern="^(live|otc|all)$")):
    if client is None or not connection_info.get("connected"):
        raise HTTPException(status_code=409, detail="Connect the API first.")
    all_assets = [_asset_dict(a) for a in client.get_assets()]
    if market == "otc":
        selected = [a for a in all_assets if a["is_otc"]]
    elif market == "live":
        selected = [a for a in all_assets if not a["is_otc"]]
    else:
        selected = all_assets
    return {
        "market": market,
        "count": len(selected),
        "assets": sorted(selected, key=lambda a: a["symbol"]),
    }


@app.get("/api/chart")
async def chart(
    symbol: str = Query(min_length=2, max_length=64),
    timeframe: int = Query(default=60, ge=30, le=60),
    limit: int = Query(default=1000, ge=50, le=1000),
):
    if timeframe not in (30, 60):
        raise HTTPException(status_code=422, detail="Timeframe must be 30 or 60 seconds.")
    if client is None or not connection_info.get("connected"):
        raise HTTPException(status_code=409, detail="Connect the API first.")
    asset_symbols = {a.symbol for a in client.get_assets()}
    if symbol not in asset_symbols:
        raise HTTPException(status_code=404, detail="This symbol is not in the API instrument list.")
    try:
        # Fetch enough elapsed time for the requested number of bars.
        candles = await client.get_historical_candles(
            symbol=symbol,
            amount_of_seconds=limit * timeframe + timeframe * 10,
            period=timeframe,
            timeout=15.0,
        )
        candles = candles[-limit:]
        # Register the stream before subscribing so live ticks also build 30s/1m bars.
        client.setup_candle_stream(symbol, periods=[30, 60])
        await client.subscribe_ticks(
            symbol=symbol,
            lookback_minutes=max(200, int((limit * timeframe) / 60) + 10),
            timeframe=timeframe,
        )
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "requested": limit,
            "received": len(candles),
            "candles": [
                {"time": c.time, "open": c.open, "high": c.high, "low": c.low, "close": c.close}
                for c in candles
            ],
            "latest_tick": last_quote.get(symbol),
            "stream_subscribed": True,
            "note": "The provider may return fewer than 1,000 candles if its history is limited.",
        }
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Chart data request failed: {str(exc)[:300]}")


@app.get("/api/signal")
async def generate_signal(
    symbol: str = Query(min_length=2, max_length=64),
    timeframe: int = Query(default=60),
):
    if timeframe not in (30, 60):
        raise HTTPException(status_code=422, detail="Timeframe must be 30 or 60 seconds.")
    if client is None or not connection_info.get("connected"):
        raise HTTPException(status_code=409, detail="Connect the TradoWix API first.")
    assets = {a.symbol: a for a in client.get_assets()}
    asset = assets.get(symbol)
    if asset is None:
        raise HTTPException(status_code=404, detail="Pair is not in the authenticated instrument list.")
    if not asset.is_active or not asset.is_open:
        raise HTTPException(status_code=409, detail="This pair is currently closed or inactive.")
    try:
        candles = await client.get_historical_candles(
            symbol=symbol, amount_of_seconds=120 * timeframe,
            period=timeframe, timeout=18.0,
        )
        payload = [{"time": c.time, "open": c.open, "high": c.high,
                    "low": c.low, "close": c.close} for c in candles]
        result = analyze(payload, timeframe)
        return {"symbol": symbol, "timeframe": timeframe, **result}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Signal analysis failed: {str(exc)[:250]}")


@app.get("/api/ticks/{symbol:path}")
async def latest_tick(symbol: str):
    return {"tick": last_quote.get(symbol), "subscribed": bool(client and symbol in client._subscribed_symbols)}


@app.post("/api/disconnect")
async def disconnect_api():
    global client
    async with client_lock:
        if client is not None:
            try:
                await client.close()
            finally:
                client = None
        connection_info.update({
            "connected": False, "authenticated": False, "assets_loaded": False,
            "message": "Disconnected by user.", "connected_at": None,
        })
    return await status_payload()
