# -*- coding: utf-8 -*-
"""عميل Alpaca مبسّط (قراءة بيانات + أوامر Paper اختيارية).

- البيانات: IEX لحظي (مجاني). IEX بورصة واحدة فقط، فالأحجام جزئية لكن النِّسب (RVOL) متّسقة.
- لا تُختلق أي أسعار: أي فشل يرجع خطأ واضح، والبوت المعني يقول «غير متوفر».
- الأوامر: فقط على paper-api، وفقط إذا فُعّل place_paper_orders في config.json.
"""
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import pandas as pd
import requests

ET = ZoneInfo("America/New_York")
RIYADH = ZoneInfo("Asia/Riyadh")
UTC = timezone.utc
PAPER = "https://paper-api.alpaca.markets"
DATA = "https://data.alpaca.markets"


class ApiError(Exception):
    pass


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(s: str) -> datetime:
    s = s.replace("Z", "+00:00")
    if "." in s:  # Alpaca يرسل نانوثانية أحياناً
        head, tail = s.split(".", 1)
        frac, tz = (tail[:-6], tail[-6:]) if ("+" in tail or "-" in tail) else (tail, "")
        s = f"{head}.{frac[:6]}{tz}"
    return datetime.fromisoformat(s).astimezone(UTC)


def bars_to_df(rows: list) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame(rows).rename(columns={"t": "time", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df.set_index("time")[["open", "high", "low", "close", "volume"]].astype(float).sort_index()


class Alpaca:
    def __init__(self, key: str, secret: str, session: Optional[requests.Session] = None):
        self.s = session or requests.Session()
        self.s.headers.update({"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret, "Accept": "application/json"})
        self.calls = 0

    def _req(self, method: str, base: str, path: str, params=None, body=None, retries: int = 4):
        delay = 1.0
        for _ in range(retries):
            try:
                self.calls += 1
                r = self.s.request(method, base + path, params=params, json=body, timeout=20)
            except (requests.ConnectionError, requests.Timeout):
                time.sleep(delay); delay *= 2
                continue
            if r.status_code in (200, 201):
                return r.json()
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(float(r.headers.get("Retry-After", delay))); delay *= 2
                continue
            raise ApiError(f"HTTP {r.status_code} {path}: {r.text[:200]}")
        raise ApiError(f"فشل بعد {retries} محاولات: {path}")

    def get(self, base, path, params=None):
        return self._req("GET", base, path, params=params)

    # ---------------- السوق ----------------
    def clock(self) -> dict:
        return self.get(PAPER, "/v2/clock")

    def most_actives(self, top=50, by="volume") -> List[str]:
        d = self.get(DATA, "/v1beta1/screener/stocks/most-actives", {"by": by, "top": top})
        return [x["symbol"] for x in d.get("most_actives", [])]

    def gainers(self, top=30) -> List[str]:
        d = self.get(DATA, "/v1beta1/screener/stocks/movers", {"top": top})
        return [x["symbol"] for x in d.get("gainers", [])]

    def snapshots(self, symbols: List[str], feed="iex") -> dict:
        out = {}
        for i in range(0, len(symbols), 100):
            out.update(self.get(DATA, "/v2/stocks/snapshots", {"symbols": ",".join(symbols[i:i + 100]), "feed": feed}) or {})
        return out

    def bars(self, symbols: List[str], timeframe: str, start: datetime, end: Optional[datetime] = None,
             feed="iex") -> Dict[str, pd.DataFrame]:
        params = {"symbols": ",".join(symbols), "timeframe": timeframe, "start": iso(start), "limit": 10000,
                  "adjustment": "raw", "feed": feed, "sort": "asc"}
        if end:
            params["end"] = iso(end)
        merged: Dict[str, list] = {}
        for _ in range(30):
            d = self.get(DATA, "/v2/stocks/bars", params)
            for sym, rows in (d.get("bars") or {}).items():
                merged.setdefault(sym, []).extend(rows)
            if not d.get("next_page_token"):
                break
            params["page_token"] = d["next_page_token"]
        return {s: bars_to_df(r) for s, r in merged.items()}

    def news(self, symbols: List[str], hours=24) -> list:
        start = datetime.now(UTC) - timedelta(hours=hours)
        d = self.get(DATA, "/v1beta1/news", {"symbols": ",".join(symbols), "start": iso(start), "limit": 50, "sort": "desc"})
        return d.get("news") or []

    # ---------------- أوامر Paper (اختيارية) ----------------
    def paper_bracket(self, symbol: str, qty: int, entry: float, stop: float, target: float) -> dict:
        """أمر شراء stop-limit عند الاختراق مع وقف وهدف. paper-api فقط."""
        body = {"symbol": symbol, "qty": str(qty), "side": "buy", "type": "stop_limit", "time_in_force": "day",
                "stop_price": f"{entry:.2f}", "limit_price": f"{entry * 1.003:.2f}", "order_class": "bracket",
                "take_profit": {"limit_price": f"{target:.2f}"}, "stop_loss": {"stop_price": f"{stop:.2f}"}}
        return self._req("POST", PAPER, "/v2/orders", body=body, retries=1)
