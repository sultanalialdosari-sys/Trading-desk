# -*- coding: utf-8 -*-
"""Finnhub (الخطة المجانية): مصدر ثاني للسعر اللحظي + أخبار الشركات + تقويم النتائج.

الحد المجاني ~60 طلب/دقيقة — نستخدم أقل من 50. أي فشل يرجع None ولا يوقف المكتب.
الشموع عند Finnhub مدفوعة، فما نستخدمها: الشموع تبقى من Alpaca.
"""
import os
import time
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional

import requests

BASE = "https://finnhub.io/api/v1"


class Finnhub:
    def __init__(self, key: Optional[str] = None, session=None, per_min: int = 50):
        self.key = key or os.environ.get("FINNHUB_API_KEY")
        self.s = session or requests.Session()
        self.gap = 60.0 / per_min
        self._last = 0.0
        self.calls = 0
        self.errors: List[str] = []
        self._earn: Optional[Dict[str, list]] = None

    @property
    def ok(self) -> bool:
        return bool(self.key)

    def _get(self, path: str, params: dict):
        if not self.key:
            return None
        wait = self.gap - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        self.calls += 1
        try:
            r = self.s.get(BASE + path, params={**params, "token": self.key}, timeout=15)
            if r.status_code == 200:
                return r.json()
            self.errors.append(f"Finnhub {path}: HTTP {r.status_code}")
        except Exception as e:
            self.errors.append(f"Finnhub {path}: {type(e).__name__}")
        return None

    def quote(self, sym: str) -> Optional[dict]:
        """{'price', 'time' (UTC), 'prev_close'} أو None."""
        d = self._get("/quote", {"symbol": sym})
        if not d or not d.get("c") or not d.get("t"):
            return None
        return {"price": float(d["c"]), "time": datetime.fromtimestamp(int(d["t"]), tz=timezone.utc),
                "prev_close": float(d["pc"]) if d.get("pc") else None}

    def news(self, sym: str, now: datetime) -> Optional[list]:
        """أخبار آخر يومين بصيغة Alpaca news (عشان بوت News يقرأها مثل بعض)."""
        d = self._get("/company-news", {"symbol": sym, "from": (now - timedelta(days=2)).date().isoformat(),
                                        "to": now.date().isoformat()})
        if d is None:
            return None
        out = []
        for n in d[:20]:
            if not n.get("datetime") or not n.get("headline"):
                continue
            out.append({"symbols": [sym], "headline": n["headline"], "summary": n.get("summary", ""),
                        "source": f"{n.get('source', '')} (Finnhub)",
                        "created_at": datetime.fromtimestamp(int(n["datetime"]), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
        return out

    def earnings(self, sym: str, today: date) -> Optional[dict]:
        """أقرب إعلان نتائج من أمس لين بعد 3 أيام: {'date','hour'} — hour: bmo قبل الافتتاح / amc بعد الإغلاق."""
        if self._earn is None:
            d = self._get("/calendar/earnings", {"from": (today - timedelta(days=1)).isoformat(),
                                                 "to": (today + timedelta(days=3)).isoformat()})
            if d is None:
                return None
            self._earn = {}
            for e in d.get("earningsCalendar") or []:
                self._earn.setdefault(e.get("symbol"), []).append(e)
        got = sorted(self._earn.get(sym, []), key=lambda e: e.get("date", ""))
        return {"date": got[0].get("date"), "hour": got[0].get("hour") or ""} if got else {}


def merge_news(a: Optional[list], b: Optional[list]) -> Optional[list]:
    """يدمج أخبار Alpaca و Finnhub ويحذف المكرر (نفس العنوان تقريباً)."""
    if a is None and b is None:
        return None
    seen, out = set(), []
    for n in (a or []) + (b or []):
        k = "".join(ch for ch in (n.get("headline") or "").lower() if ch.isalnum())[:60]
        if k and k not in seen:
            seen.add(k)
            out.append(n)
    out.sort(key=lambda n: n.get("created_at", ""), reverse=True)
    return out
