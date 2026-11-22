# -*- coding: utf-8 -*-
"""المؤشرات الفنية على شموع 5 دقائق للجلسة النظامية."""
import numpy as np
import pandas as pd

from desk.alpaca import ET


def regular_session(df: pd.DataFrame) -> pd.DataFrame:
    """يحوّل لتوقيت نيويورك ويُبقي 09:30–16:00 فقط."""
    if df.empty:
        return df
    d = df.copy()
    d.index = d.index.tz_convert(ET)
    m = d.index.indexer_between_time("09:30", "15:55")
    return d.iloc[m]


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rsi(close, n=14):
    diff = close.diff()
    au = diff.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    ad = (-diff).clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    r = 100 - 100 / (1 + au / ad.replace(0, np.nan))
    return r.fillna(100.0).where(au.notna())


def atr(df, n=14):
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def vwap(df):
    tp = (df["high"] + df["low"] + df["close"]) / 3
    day = df.index.date
    return ((tp * df["volume"]).groupby(day).cumsum() / df["volume"].groupby(day).cumsum().replace(0, np.nan)).ffill()


def rvol_now(df) -> float:
    """الحجم التراكمي اليوم حتى آخر شمعة ÷ متوسط التراكمي لنفس التوقيت في الأيام السابقة (حتى 10)."""
    if df.empty:
        return float("nan")
    days = sorted(set(df.index.date))
    today = days[-1]
    t_last = df.index[-1].time()
    cur = df[df.index.date == today]["volume"].sum()
    prev = []
    for dd in days[:-1][-10:]:
        x = df[(df.index.date == dd)]
        x = x[[t <= t_last for t in x.index.time]]
        if len(x):
            prev.append(x["volume"].sum())
    base = np.mean(prev) if len(prev) >= 3 else np.nan
    return float(cur / base) if base and base > 0 else float("nan")


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["vwap"] = vwap(d)
    d["ema9"] = ema(d["close"], 9)
    d["ema20"] = ema(d["close"], 20)
    d["rsi"] = rsi(d["close"])
    d["atr"] = atr(d)
    return d


def levels(d: pd.DataFrame) -> dict:
    """أهم المستويات: قمة/قاع أمس، قمة/قاع اليوم، الافتتاح."""
    days = sorted(set(d.index.date))
    today = d[d.index.date == days[-1]]
    out = {"day_high": float(today["high"].max()), "day_low": float(today["low"].min()),
           "day_open": float(today["open"].iloc[0])}
    if len(days) >= 2:
        y = d[d.index.date == days[-2]]
        out.update({"prev_high": float(y["high"].max()), "prev_low": float(y["low"].min()), "prev_close": float(y["close"].iloc[-1])})
    return out
