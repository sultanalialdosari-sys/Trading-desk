# -*- coding: utf-8 -*-
"""SEC EDGAR (مجاني، بدون مفتاح): يكشف إفصاحات طرح الأسهم (تخفيف) اللي غالباً تكسر الصعود.

SEC تشترط User-Agent فيه بريد للتواصل: ضيف SEC_USER_AGENT مثل «Rayan rayan@example.com».
بدونه الفحص يتوقف ويكتب «غير متوفر» (ما نخمّن).
"""
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import requests

TICKERS = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS = "https://data.sec.gov/submissions/CIK{:010d}.json"

# (النماذج، خلال كم يوم، الشدة، الوصف)
RULES = [
    ({"424B1", "424B2", "424B3", "424B4", "424B5", "424B7"}, 5, "veto", "نشرة طرح أسهم ({form}) بتاريخ {date} — طرح جاري، يعني ضغط بيع"),
    ({"S-1", "S-1/A", "F-1", "F-1/A"}, 30, "major", "تسجيل طرح أسهم ({form}) بتاريخ {date} — طرح محتمل قريب"),
    ({"S-3", "S-3/A", "S-3ASR", "F-3"}, 30, "minor", "تسجيل رف ({form}) بتاريخ {date} — تقدر الشركة تطرح أسهم بأي وقت"),
    ({"25-NSE", "25"}, 90, "veto", "إشعار شطب ({form}) بتاريخ {date}"),
]


class Sec:
    def __init__(self, user_agent: Optional[str] = None, session=None):
        ua = (user_agent or os.environ.get("SEC_USER_AGENT") or "").strip()
        self.ok = "@" in ua
        self.s = session or requests.Session()
        self.s.headers.update({"User-Agent": ua or "trading-desk", "Accept-Encoding": "gzip, deflate"})
        self._map: Optional[Dict[str, int]] = None
        self._last = 0.0
        self.errors: List[str] = []

    def _get(self, url):
        wait = 0.15 - (time.monotonic() - self._last)   # أقل من 10 طلبات/ثانية
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        try:
            r = self.s.get(url, timeout=20)
            if r.status_code == 200:
                return r.json()
            self.errors.append(f"SEC HTTP {r.status_code}")
        except Exception as e:
            self.errors.append(f"SEC {type(e).__name__}")
        return None

    def cik(self, sym: str) -> Optional[int]:
        if self._map is None:
            d = self._get(TICKERS) or {}
            self._map = {v["ticker"].upper(): int(v["cik_str"]) for v in d.values()} if d else {}
        return self._map.get(sym.upper().replace(".", "-"))

    def risks(self, sym: str, now: datetime) -> Optional[List[dict]]:
        """قائمة مخاطر [{severity, text}] — [] = ما فيه، None = غير متوفر."""
        if not self.ok:
            return None
        c = self.cik(sym)
        if c is None:
            return None if not self._map else []
        d = self._get(SUBMISSIONS.format(c))
        if not d:
            return None
        rec = d.get("filings", {}).get("recent", {})
        out = []
        for form, fdate, acc in zip(rec.get("form", []), rec.get("filingDate", []), rec.get("acceptanceDateTime", [])):
            try:
                t = datetime.fromisoformat(acc.replace("Z", "+00:00")) if acc else datetime.fromisoformat(fdate).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if t > now:          # لا نستخدم إفصاح بعد لحظة القرار
                continue
            age = (now - t).days
            for forms, days, sev, text in RULES:
                if form in forms and age <= days:
                    out.append({"severity": sev, "text": text.format(form=form, date=fdate)})
                    break
            if age > 90:
                break
        # الأشد أولاً، وبدون تكرار نفس النوع
        order = {"veto": 0, "major": 1, "minor": 2}
        uniq, seen = [], set()
        for x in sorted(out, key=lambda x: order[x["severity"]]):
            k = x["text"].split("(")[0]
            if k not in seen:
                seen.add(k)
                uniq.append(x)
        return uniq[:3]
