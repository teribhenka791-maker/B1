"""Deterministic candle-based technical signal engine (not a win-rate model)."""
from __future__ import annotations
from math import sqrt
from typing import Any


def _ema(values: list[float], period: int) -> list[float]:
    alpha = 2 / (period + 1)
    out = [values[0]]
    for value in values[1:]:
        out.append(alpha * value + (1 - alpha) * out[-1])
    return out


def analyze(candles: list[dict[str, Any]], timeframe: int) -> dict[str, Any]:
    if len(candles) < 60:
        raise ValueError(f"At least 60 real candles are required; received {len(candles)}.")
    closes = [float(c['close']) for c in candles]
    highs = [float(c['high']) for c in candles]
    lows = [float(c['low']) for c in candles]
    opens = [float(c['open']) for c in candles]
    if not all(map(lambda x: x > 0, closes)):
        raise ValueError("Candle feed contains invalid prices.")

    e9, e21, e50 = _ema(closes, 9), _ema(closes, 21), _ema(closes, 50)
    gains, losses = [], []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i-1]
        gains.append(max(change, 0)); losses.append(max(-change, 0))
    avg_gain = sum(gains[-14:]) / 14
    avg_loss = sum(losses[-14:]) / 14
    rsi = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)

    macd_line = _ema(closes, 12)[-1] - _ema(closes, 26)[-1]
    macd_series = [a-b for a,b in zip(_ema(closes,12), _ema(closes,26))]
    macd_signal = _ema(macd_series, 9)[-1]
    macd_hist = macd_line - macd_signal

    sample = closes[-20:]
    mean = sum(sample) / len(sample)
    sd = sqrt(sum((v-mean)**2 for v in sample) / len(sample))
    upper, lower = mean + 2*sd, mean - 2*sd

    trs = []
    for i in range(1, len(candles)):
        trs.append(max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])))
    atr = sum(trs[-14:]) / 14

    score = 0.0
    reasons: list[str] = []
    if e9[-1] > e21[-1] > e50[-1]: score += 2; reasons.append("EMA 9/21/50 bullish alignment")
    elif e9[-1] < e21[-1] < e50[-1]: score -= 2; reasons.append("EMA 9/21/50 bearish alignment")
    if rsi >= 55: score += 1; reasons.append(f"RSI bullish ({rsi:.1f})")
    elif rsi <= 45: score -= 1; reasons.append(f"RSI bearish ({rsi:.1f})")
    if macd_hist > 0: score += 1; reasons.append("MACD histogram positive")
    elif macd_hist < 0: score -= 1; reasons.append("MACD histogram negative")
    if closes[-1] > upper: score += 1; reasons.append("Close above Bollinger upper band")
    elif closes[-1] < lower: score -= 1; reasons.append("Close below Bollinger lower band")

    recent_high, recent_low = max(highs[-20:-1]), min(lows[-20:-1])
    if closes[-1] > recent_high: score += 1.5; reasons.append("20-candle resistance breakout")
    elif closes[-1] < recent_low: score -= 1.5; reasons.append("20-candle support breakdown")

    body = closes[-1] - opens[-1]
    if atr and body > atr * .25: score += .5; reasons.append("Bullish candle momentum")
    elif atr and body < -atr * .25: score -= .5; reasons.append("Bearish candle momentum")

    direction = "CALL" if score >= 0 else "PUT"
    # This is a rule-strength estimate, not a statistically validated probability.
    confidence = min(88, 52 + round(abs(score) * 5))
    return {
        "signal": direction, "confidence": confidence, "score": round(score, 2),
        "entry_price": closes[-1], "expiry_seconds": timeframe,
        "indicators": {"ema9": e9[-1], "ema21": e21[-1], "ema50": e50[-1], "rsi14": round(rsi,2),
                       "macd_histogram": macd_hist, "bollinger_upper": upper, "bollinger_lower": lower, "atr14": atr},
        "reasons": reasons, "candles_used": len(candles),
        "confidence_note": "Rule-strength estimate only; not a proven win probability.",
    }
