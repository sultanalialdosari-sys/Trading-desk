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
APE = "https://apewisdom.io/api/v1.0/filter/all-stocks/page/{}"


class Reddit:
    """ApeWisdom (مجاني بدون مفتاح): كم مرة انذكر السهم في Reddit آخر 24 ساعة، مقارنة بأمس."""

    def __init__(self, session=None, pages: int = 3):
        self.s, self.pages = session or requests, pages
        self._map = None

    def get(self, sym: str) -> Optional[dict]:
        if self._map is None:
            self._map = {}
            for p in range(1, self.pages + 1):
                try:
                    r = self.s.get(APE.format(p), timeout=10, headers={"User-Agent": "Mozilla/5.0 trading-desk"})
                    if r.status_code != 200:
                        break
                    for x in r.json().get("results", []):
                        self._map[(x.get("ticker") or "").upper()] = x
                except Exception:
                    break
            if not self._map:
                self._map = None
                return None
        x = self._map.get(sym.upper())
        if not x:
            return {"mentions": 0}
        return {"mentions": int(x.get("mentions") or 0), "mentions_24h_ago": int(x.get("mentions_24h_ago") or 0),
                "rank": x.get("rank"), "rank_24h_ago": x.get("rank_24h_ago")}


def reddit_txt(rd: Optional[dict]) -> str:
    if rd is None:
        return ""
    if not rd.get("mentions"):
        return " Reddit: ما انذكر."
    prev = rd.get("mentions_24h_ago") or 0
    jump = f"، {rd['mentions'] / prev:.1f}× أمس" if prev else ""
    return f" Reddit: {rd['mentions']} ذكر (المركز {rd.get('rank')}{jump})."


def fetch(sym: str, session: Optional[requests.Session] = None) -> Optional[dict]:
    s = session or requests
    try:
        r = s.get(URL.format(sym), timeout=10, headers={"User-Agent": "Mozilla/5.0 trading-desk"})
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def analyze(sym: str, data: Optional[dict], now: datetime, reddit: Optional[dict] = None) -> Opinion:
    op = _analyze(sym, data, now)
    if reddit is not None:
        op.facts["reddit"] = reddit
        prev = reddit.get("mentions_24h_ago") or 0
        op.facts["reddit_surge"] = bool(reddit.get("mentions", 0) >= 20 and (prev == 0 or reddit["mentions"] >= 3 * prev))
        if op.score is None:   # StockTwits ما ردّ — Reddit يعطي الاهتمام بس (مو الاتجاه)
            m = reddit.get("mentions", 0)
            op.stance, op.score = "neutral", 50.0 + min(10, m / 10)
            op.line = "StockTwits ما ردّ."
        op.line += reddit_txt(reddit)
    return op


def _analyze(sym: str, data: Optional[dict], now: datetime) -> Opinion:
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
