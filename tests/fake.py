# -*- coding: utf-8 -*-
"""سوق وهمي للاختبار — بدون إنترنت."""
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from desk.alpaca import ET, UTC

NOW = datetime(2026, 11, 24, 11, 2, tzinfo=ET).astimezone(UTC)


def _session_index(day):
    t0 = datetime(day.year, day.month, day.day, 9, 30, tzinfo=ET)
    return [t0 + timedelta(minutes=5 * i) for i in range(78)]


def make_bars(kind: str, seed: int, now=NOW, base=50.0):
    rng = np.random.default_rng(seed)
    rows = []
    day = now.astimezone(ET).date() - timedelta(days=13)
    price = base
    while day <= now.astimezone(ET).date():
        if day.weekday() < 5:
            is_today = day == now.astimezone(ET).date()
            idx = _session_index(day)
            vol_mult = 1.0
            for k, ts in enumerate(idx):
                if ts.astimezone(UTC) + timedelta(minutes=5) > now + timedelta(minutes=5):
                    break
                drift = 0.0
                if is_today:
                    vol_mult = 3.0 if kind in ("trend", "risk") else 1.0
                    if kind in ("trend", "risk"):
                        drift = 0.0025 if k < 12 else (-0.002 if k < 16 else 0.0015)
                    elif kind == "dump":
                        drift = -0.003
                o = price
                c = o * (1 + drift + rng.normal(0, 0.0012))
                h = max(o, c) * (1 + abs(rng.normal(0, 0.0008)))
                l = min(o, c) * (1 - abs(rng.normal(0, 0.0008)))
                v = float(rng.integers(20000, 40000)) * vol_mult
                rows.append({"time": ts.astimezone(UTC), "open": o, "high": h, "low": l, "close": c, "volume": v})
                price = c
        day += timedelta(days=1)
    df = pd.DataFrame(rows).set_index("time")
    df.index = pd.DatetimeIndex(df.index)
    return df


class FakeAlpaca:
    calls = 0

    def __init__(self, now=NOW):
        self.now = now
        self.data = {"TRND": make_bars("trend", 1), "FLAT": make_bars("flat", 2, base=30),
                     "RISK": make_bars("risk", 3, base=20), "DUMP": make_bars("dump", 4, base=80),
                     "SPY": make_bars("trend", 5, base=600), "QQQ": make_bars("trend", 6, base=520)}
        self.orders = []

    def clock(self):
        return {"is_open": True}

    def most_actives(self, top=50, by="volume"):
        return ["TRND", "FLAT", "RISK", "DUMP", "SPY"]

    def gainers(self, top=30):
        return ["TRND"]

    def snapshots(self, symbols, feed="iex"):
        out = {}
        for s in symbols:
            d = self.data.get(s)
            if d is None:
                continue
            days = sorted(set(d.index.tz_convert(ET).date))
            t = d[d.index.tz_convert(ET).date == days[-1]]
            y = d[d.index.tz_convert(ET).date == days[-2]]
            out[s] = {"latestTrade": {"p": float(t["close"].iloc[-1]), "t": (self.now - timedelta(seconds=20)).strftime("%Y-%m-%dT%H:%M:%SZ")},
                      "dailyBar": {"c": float(t["close"].iloc[-1]), "h": float(t["high"].max()), "v": float(t["volume"].sum()), "vw": float(t["close"].mean())},
                      "prevDailyBar": {"c": float(y["close"].iloc[-1]), "v": float(y["volume"].sum())}}
        return out

    def bars(self, symbols, timeframe, start, end=None, feed="iex"):
        return {s: self.data[s][self.data[s].index >= start] for s in symbols if s in self.data}

    def news(self, symbols, hours=24):
        n = [{"symbols": ["TRND"], "headline": "TRND beats estimates and raises guidance", "summary": "",
              "source": "benzinga", "created_at": (self.now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")},
             {"symbols": ["RISK"], "headline": "RISK announces $50 million public offering", "summary": "",
              "source": "benzinga", "created_at": (self.now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")}]
        return [x for x in n if set(x["symbols"]) & set(symbols)]

    def paper_bracket(self, *a):
        self.orders.append(a)
        return {"id": "x"}


class FakeTG:
    ok = True

    def __init__(self):
        self.sent, self.updates, self.marks = [], [], []

    def poll(self, offset):
        u = [x for x in self.updates if x["update_id"] >= offset]
        return u

    def send_idea(self, idea, site_url=""):
        from desk.telegram import format_idea
        self.sent.append(format_idea(idea, site_url))
        return len(self.sent)

    def send(self, text):
        self.sent.append(text)

    def ack(self, *a):
        pass

    def mark(self, chat, mid, note, original):
        self.marks.append(note)


def fake_senti(sym):
    if sym == "TRND":
        msgs = [{"entities": {"sentiment": {"basic": "Bullish" if i % 4 else "Bearish"}},
                 "created_at": (NOW - timedelta(minutes=3 * i)).strftime("%Y-%m-%dT%H:%M:%SZ")} for i in range(30)]
        return {"messages": msgs, "symbol": {"watchlist_count": 12000}}
    return None
