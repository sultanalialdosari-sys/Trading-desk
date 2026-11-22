# -*- coding: utf-8 -*-
"""📈 Charts — يقرأ شموع 5 دقائق ويحدد إن كان فيه إعداد دخول (شراء فقط)، مع الدخول والستوب والهدف.

إعدادان:
 • ارتداد (pullback): اتجاه صاعد (فوق VWAP و EMA9>EMA20) والسعر رجع لمس EMA9/VWAP وصمد.
 • اختراق (breakout): السعر قريب من قمة اليوم/أمس فوق VWAP.
الدخول دائماً «فوق» سعر محدد (stop entry) — يعني ما ندخل إلا إذا السوق أكّد.
"""
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from desk import ta
from desk.bots.base import Opinion, px


def _round(p: float) -> float:
    return round(p, 2) if p >= 1 else round(p, 4)


def drop_forming(df: pd.DataFrame, now: datetime) -> pd.DataFrame:
    if df.empty:
        return df
    last_start = df.index[-1].to_pydatetime()
    return df.iloc[:-1] if last_start + timedelta(minutes=5) > now else df


def analyze(sym: str, raw5: pd.DataFrame, now: datetime, cfg: dict) -> Opinion:
    d = ta.regular_session(drop_forming(raw5, now))
    if d.empty or len(set(d.index.date)) < 2:
        return Opinion("charts", "unavailable", None, "ما عندي شموع كافية أحكم عليها.", {"symbol": sym})
    today = d[d.index.date == d.index[-1].date()]
    if d.index[-1].date() != now.astimezone(d.index.tz).date() or len(today) < 3:
        return Opinion("charts", "unavailable", None, "جلسة اليوم ما زالت في أولها — أنتظر 3 شموع على الأقل.", {"symbol": sym})
    d = ta.enrich(d)
    return plan_at(sym, d, ta.rvol_now(d), cfg)


def plan_at(sym: str, d: pd.DataFrame, rv: float, cfg: dict) -> Opinion:
    """الجزء الحسابي من Charts على شموع جلسة «مُثرّاة» بالمؤشرات تنتهي عند آخر شمعة مقفلة (يستخدمه الاختبار التاريخي مباشرة)."""
    last = d.iloc[-1]
    a = float(last["atr"]) if not np.isnan(last["atr"]) else None
    if not a or a <= 0:
        return Opinion("charts", "unavailable", None, "ATR غير متوفر.", {"symbol": sym})
    lv = ta.levels(d)
    close, vw, e9, e20, r = (float(last[k]) for k in ("close", "vwap", "ema9", "ema20", "rsi"))
    trend_up = close > vw and e9 > e20
    recent = d.tail(6)
    plan, kind = None, None

    # ارتداد
    touched = (d.tail(2)["low"] <= max(e9, vw) + 0.15 * a).any()
    if trend_up and touched and close > e9:
        entry = float(d.tail(1)["high"].iloc[0]) + 0.05 * a
        stop = min(float(d.tail(3)["low"].min()), vw) - 0.1 * a
        plan, kind = (entry, stop), "pullback"
        # قمة اليوم قريبة فوق الدخول؟ الدخول الحقيقي يصير عند اختراقها (وإلا نشتري تحت سقف)
        if lv["day_high"] > entry and lv["day_high"] - entry < (entry - stop):
            plan, kind = (lv["day_high"] + max(0.05 * a, 0.01), stop), "pullback_breakout"
    # اختراق
    res = lv["day_high"]
    if plan is None and close > vw and 0 <= res - close <= 0.6 * a:
        entry = res + max(0.05 * a, 0.01)
        stop = float(recent["low"].min()) - 0.05 * a
        plan, kind = (entry, stop), "breakout"

    score = 50.0
    score += 15 if trend_up else -15
    if not np.isnan(rv):
        score += 15 if rv >= 2.5 else 10 if rv >= 1.5 else -10 if rv < 1 else 0
    if r is not None and not np.isnan(r):
        score += 10 if 50 <= r <= 70 else -10 if r > 75 or r < 40 else 0
    ext = (close - vw) / a
    if ext > 2:
        score -= 15
    if plan:
        score += 10
    score = max(0, min(100, score))

    facts = {"symbol": sym, "close": _round(close), "vwap": _round(vw), "ema9": _round(e9), "ema20": _round(e20),
             "rsi": round(r, 1) if not np.isnan(r) else None, "atr": _round(a),
             "rvol": round(rv, 2) if not np.isnan(rv) else None, "trend_up": trend_up,
             "extension_atr": round(ext, 2), "setup": kind, "bar_time_et": d.index[-1].strftime("%H:%M"), **{k: _round(v) for k, v in lv.items()}}

    trend_txt = "الاتجاه صاعد (فوق VWAP و EMA9 فوق EMA20)" if trend_up else ("فوق VWAP بس المتوسطات ما تأكدت" if close > vw else "تحت VWAP — الاتجاه مو معنا")
    rv_txt = f"RVOL {rv:.1f}×" if not np.isnan(rv) else "RVOL غير متوفر"
    if not plan:
        return Opinion("charts", "negative" if not trend_up else "neutral", round(score, 1),
                       f"{trend_txt}، {rv_txt}. ما فيه نقطة دخول واضحة الحين.", facts)

    entry, stop = _round(plan[0]), _round(plan[1])
    risk = entry - stop
    target = _round(entry + cfg["target_r"] * risk)
    # المقاومة التالية فوق الدخول
    above = [v for v in (lv.get("prev_high"), lv["day_high"]) if v and v > entry + 0.1 * a]
    next_res = _round(min(above)) if above else None
    facts.update({"entry": entry, "stop": stop, "target": target, "risk_per_share": _round(risk),
                  "rr": round((target - entry) / risk, 2) if risk > 0 else None, "next_resistance": next_res})
    kind_txt = {"pullback": "ارتداد من EMA9/VWAP",
                "pullback_breakout": f"ارتداد من EMA9/VWAP، والدخول عند اختراق قمة اليوم {px(lv['day_high'])}",
                }.get(kind, f"اختراق قمة اليوم {px(lv['day_high'])}")
    line = (f"{trend_txt}، {rv_txt}، RSI {facts['rsi']}. الإعداد: {kind_txt}. "
            f"دخول فوق {px(entry)}، ستوب {px(stop)}، هدف {px(target)}.")
    return Opinion("charts", "positive" if score >= 60 else "neutral", round(score, 1), line, facts)


def retarget(f: dict, target_r: float) -> dict:
    """نفس الخطة بهدف مختلف (لمقارنة إعدادات الهدف بدون إعادة الحساب)."""
    if "entry" not in f:
        return f
    g = dict(f)
    risk = g["entry"] - g["stop"]
    g["target"] = _round(g["entry"] + target_r * risk)
    g["rr"] = round(target_r, 2) if risk > 0 else None
    return g
