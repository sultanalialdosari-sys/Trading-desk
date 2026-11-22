# -*- coding: utf-8 -*-
"""💬 Sentiment — مزاج الناس من StockTwits (API عام مجاني بدون مفتاح).

يقيس: نسبة المتفائلين/المتشائمين بين آخر الرسائل الموسومة، وسرعة الكلام (رسائل آخر ساعة).
ملاحظة: X صار مدفوعاً، فـStockTwits هو البديل المجاني. إذا رُفض الطلب يقول «غير متوفر» ولا يخمّن.
"""
from datetime import datetime, timezone
from typing import Optional

import requests

from desk.bots.base import Opinion

URL = "https://api.stocktwits.com/api/2/streams/symbol/{}.json"


def fetch(sym: str, session: Optional[requests.Session] = None) -> Optional[dict]:
    s = session or requests
    try:
        r = s.get(URL.format(sym), timeout=10, headers={"User-Agent": "Mozilla/5.0 trading-desk"})
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def analyze(sym: str, data: Optional[dict], now: datetime) -> Opinion:
    if not data or "messages" not in data:
        return Opinion("sentiment", "unavailable", None, "StockTwits ما ردّ — المزاج غير متوفر هالمرة.", {"symbol": sym})
    msgs = data["messages"]
    bull = bear = last_hour = 0
    for m in msgs:
        s = ((m.get("entities") or {}).get("sentiment") or {}) or {}
        if s.get("basic") == "Bullish":
            bull += 1
        elif s.get("basic") == "Bearish":
            bear += 1
        try:
            t = datetime.strptime(m["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            if (now - t).total_seconds() <= 3600:
                last_hour += 1
        except Exception:
            pass
    watchers = (data.get("symbol") or {}).get("watchlist_count")
    tagged = bull + bear
    facts = {"symbol": sym, "bullish": bull, "bearish": bear, "messages": len(msgs), "messages_last_hour": last_hour,
             "watchers": watchers}
    if tagged < 5:
        return Opinion("sentiment", "neutral", 50.0,
                       f"الكلام عنه قليل ({len(msgs)} رسالة، {last_hour} منها آخر ساعة) — ما فيه مزاج واضح.", facts)
    bp = bull / tagged * 100
    facts["bullish_pct"] = round(bp)
    buzz = "والكلام عنه نشط" if last_hour >= 10 else "والكلام عنه هادي"
    if bp >= 85:
        return Opinion("sentiment", "neutral", 58.0,
                       f"{round(bp)}% متفائلين ({bull} مقابل {bear}) {buzz} — تفاؤل زايد، يعني الصفقة مزدحمة.", facts)
    if bp >= 60:
        return Opinion("sentiment", "positive", 65.0 + min(10, last_hour / 3), f"{round(bp)}% متفائلين ({bull} مقابل {bear}) {buzz}.", facts)
    if bp <= 40:
        return Opinion("sentiment", "negative", 35.0, f"المزاج سلبي: {round(bp)}% فقط متفائلين ({bull} مقابل {bear}).", facts)
    return Opinion("sentiment", "neutral", 50.0, f"المزاج منقسم: {round(bp)}% متفائلين ({bull} مقابل {bear}).", facts)
