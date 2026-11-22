# -*- coding: utf-8 -*-
"""اختبار تاريخي لمكتب التداول — نعيد تشغيل نفس البوتات على الماضي، لحظة بلحظة.

مبادئ (من سكيل backtesting-frameworks):
 • بدون نظر للمستقبل: كل «فحص» يشوف فقط الشموع اللي انقفلت قبل لحظته، والأخبار المنشورة قبلها.
 • الدخول فقط عند اختراق سعر الدخول في شمعة لاحقة، والمحاكاة متحفظة (لمس الستوب والهدف في شمعة = خسارة).
 • تكاليف: انزلاق slippage_pct لكل جهة.
 • walk-forward: نختار الإعدادات على فترة، ونحكم عليها على الفترة اللي بعدها فقط.

حدود معروفة (تنكتب في التقرير):
 • Sentiment غير متوفر تاريخياً (StockTwits ما يعطي الماضي) — يُستبعد ويتوزع وزنه.
 • Scout الحي يختار من قوائم «الأكثر نشاطاً» اللحظية؛ تاريخياً نستخدم قائمة ثابتة من أسهم سائلة
   (تحيّز اختيار + تحيّز البقاء: كلها أسهم موجودة اليوم).
"""
import math
import random
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from itertools import product
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from desk.alpaca import ET, UTC, parse_ts
from desk.bots import charts, chief, news, scout, skeptic
from desk.bots.base import Opinion
from desk.engine import Desk
from desk.ledger import _close, step_trade

SCAN_EVERY_MIN = 15


@dataclass
class Snap:
    now: datetime
    sym: str
    so: Opinion
    ch: Opinion
    nw: Opinion
    mkt: dict
    day_bars: pd.DataFrame  # شموع اليوم كاملة (للمحاكاة بعد اللحظة فقط)


def scan_times(day) -> List[datetime]:
    t = datetime.combine(day, time(9, 45), tzinfo=ET)
    end = datetime.combine(day, time(15, 45), tzinfo=ET)
    out = []
    while t <= end:
        out.append((t + timedelta(minutes=1)).astimezone(UTC))
        t += timedelta(minutes=SCAN_EVERY_MIN)
    return out


def _upto(df: pd.DataFrame, now: datetime, days: int = 12) -> pd.DataFrame:
    """شموع انتهت قبل now، لآخر `days` يوم (point-in-time)."""
    if df is None or df.empty:
        return pd.DataFrame()
    hi = df.index.searchsorted(pd.Timestamp(now) - pd.Timedelta(minutes=5), side="right")
    lo = df.index.searchsorted(pd.Timestamp(now) - pd.Timedelta(days=days), side="left")
    return df.iloc[lo:hi]


def scout_from_bars(sym: str, past: pd.DataFrame, now: datetime, cfg: dict) -> Optional[Opinion]:
    d = past.copy()
    d.index = d.index.tz_convert(ET)
    d = d.iloc[d.index.indexer_between_time("09:30", "15:55")]
    days = sorted(set(d.index.date))
    if len(days) < 2 or days[-1] != now.astimezone(ET).date():
        return None
    t, y = d[d.index.date == days[-1]], d[d.index.date == days[-2]]
    vw = float((t["close"] * t["volume"]).sum() / max(t["volume"].sum(), 1))
    snap = {sym: {"latestTrade": {"p": float(t["close"].iloc[-1]), "t": now.strftime("%Y-%m-%dT%H:%M:%SZ")},
                  "dailyBar": {"c": float(t["close"].iloc[-1]), "h": float(t["high"].max()), "v": float(t["volume"].sum()), "vw": vw},
                  "prevDailyBar": {"c": float(y["close"].iloc[-1]), "v": float(y["volume"].sum())}}}
    got = scout.scan(snap, [], cfg, now)
    return got[0] if got else None


def collect(bars: Dict[str, pd.DataFrame], news_items: Optional[list], symbols: List[str], days: List,
            cfg: dict, log=print) -> List[Snap]:
    """المرحلة الثقيلة: آراء Scout/Charts/News لكل سهم في كل لحظة فحص (لا تعتمد على عتبات القرار)."""
    for n in news_items or []:
        n["_t"] = parse_ts(n["created_at"]) if n.get("created_at") else None
    snaps: List[Snap] = []
    for k, day in enumerate(days):
        day_full = {s: bars[s][bars[s].index.tz_convert(ET).date == day] for s in symbols if s in bars}
        for now in scan_times(day):
            mb = {s: _upto(bars.get(s), now) for s in ("SPY", "QQQ")}
            mkt = Desk.market(None, mb, now)
            cands = []
            for s in symbols:
                past = _upto(bars.get(s), now)
                if past.empty:
                    continue
                so = scout_from_bars(s, past, now, cfg)
                if so:
                    cands.append((so, past))
            cands.sort(key=lambda x: x[0].score, reverse=True)
            deep = []
            for so, past in cands[:cfg["scout_top"]]:
                ch = charts.analyze(so.facts["symbol"], past, now, cfg)
                if "entry" in ch.facts or (ch.score or 0) >= 60:
                    deep.append((so, ch))
            for so, ch in deep[:cfg["deep_max"]]:
                s = so.facts["symbol"]
                if news_items is None:
                    nw = news.analyze(s, None, now)
                else:
                    recent = [n for n in news_items if n["_t"] and now - timedelta(hours=24) <= n["_t"] <= now]
                    recent.sort(key=lambda n: n["_t"], reverse=True)
                    nw = news.analyze(s, recent, now)
                snaps.append(Snap(now, s, so, ch, nw, mkt, day_full.get(s, pd.DataFrame())))
        log(f"  يوم {k + 1}/{len(days)} {day} — لحظات مرشّحة حتى الآن: {len(snaps)}")
    return snaps


NO_SENTI = Opinion("sentiment", "unavailable", None, "غير متوفر تاريخياً", {})


def decide(snaps: List[Snap], cfg: dict) -> List[dict]:
    """المرحلة الخفيفة: Skeptic + Chief بالعتبات المعطاة، ثم المحاكاة. ترجع قائمة الصفقات."""
    trades, last = [], {}
    for sn in snaps:
        sk = skeptic.review(sn.sym, sn.so, sn.ch, sn.nw, NO_SENTI, sn.mkt, sn.now, cfg)
        ops = {"scout": sn.so, "charts": sn.ch, "news": sn.nw, "sentiment": NO_SENTI, "skeptic": sk}
        cf = chief.decide(sn.sym, ops, sn.mkt, cfg)
        dec, blocked, t = cf.facts["decision"], cf.facts["blocked"], cf.facts["ticket"]
        shadow = blocked and t and cf.facts["base_score"] >= cfg["wait_score"]
        if dec == "SKIP" and not shadow:
            continue
        prev = last.get(sn.sym)
        if prev and sn.now - prev[0] < timedelta(minutes=cfg["cooldown_min"]) and not (prev[1] == "WAIT" and dec == "BUY"):
            continue
        last[sn.sym] = (sn.now, dec)
        owner = "desk" if dec == "BUY" else "wait" if dec == "WAIT" else "blocked"
        tr = {"symbol": sn.sym, "owner": owner, "status": "pending", "entry": t["entry"], "stop": t["stop"],
              "target": t["target"], "qty": t["qty"], "created_utc": sn.now.isoformat(),
              "valid_until_utc": (sn.now + timedelta(minutes=cfg["valid_min"])).isoformat(), "fill_px": None,
              "score": cf.score, "setup": sn.ch.facts.get("setup"), "hour_et": sn.now.astimezone(ET).hour,
              "day": sn.now.astimezone(ET).date().isoformat(),
              "objections": [o["text"][:60] for o in sk.facts["objections"] if o["severity"] != "minor"][:2]}
        step_trade(tr, sn.day_bars, cfg["slippage_pct"])
        if tr["status"] == "pending":
            tr["status"] = "expired"
        elif tr["status"] == "open":
            _close(tr, float(sn.day_bars["close"].iloc[-1]), sn.day_bars.index[-1].isoformat(), cfg["slippage_pct"], "closed")
        trades.append(tr)
    return trades


# ====================================================================== المقاييس
def r_stats(rs: List[float]) -> dict:
    n = len(rs)
    if not n:
        return {"trades": 0}
    a = np.array(rs, dtype=float)
    eq = np.cumsum(a)
    dd = float((eq - np.maximum.accumulate(np.concatenate([[0], eq]))[1:]).min())
    streak = mx = 0
    for r in a:
        streak = streak + 1 if r <= 0 else 0
        mx = max(mx, streak)
    gw, gl = a[a > 0].sum(), -a[a < 0].sum()
    return {"trades": n, "win_rate": round(float((a > 0).mean() * 100), 1), "avg_r": round(float(a.mean()), 3),
            "total_r": round(float(a.sum()), 2), "profit_factor": round(float(gw / gl), 2) if gl > 0 else None,
            "max_dd_r": round(dd, 2), "max_losing_streak": int(mx), "best_r": round(float(a.max()), 2),
            "worst_r": round(float(a.min()), 2)}


def monte_carlo(rs: List[float], sims: int = 2000, seed: int = 7) -> dict:
    if len(rs) < 10:
        return {}
    rng = np.random.default_rng(seed)
    a = np.array(rs)
    s = rng.choice(a, size=(sims, len(a)), replace=True)
    eq = np.cumsum(s, axis=1)
    peak = np.maximum.accumulate(np.concatenate([np.zeros((sims, 1)), eq], axis=1), axis=1)[:, 1:]
    dd = (eq - peak).min(axis=1)
    return {"median_max_dd_r": round(float(np.median(dd)), 2), "worst5pct_max_dd_r": round(float(np.percentile(dd, 5)), 2),
            "prob_negative_total": round(float((eq[:, -1] < 0).mean() * 100), 1),
            "total_r_p5": round(float(np.percentile(eq[:, -1], 5)), 2), "total_r_p95": round(float(np.percentile(eq[:, -1], 95)), 2)}


def done(trades):
    return [t for t in trades if t["status"] in ("won", "lost", "closed") and t.get("r") is not None]


def breakdown(trades: List[dict], key: str) -> dict:
    out = {}
    for t in done(trades):
        out.setdefault(str(t.get(key)), []).append(t["r"])
    return {k: r_stats(v) for k, v in sorted(out.items())}


def summarize(trades: List[dict]) -> dict:
    res = {}
    for owner in ("desk", "wait", "blocked"):
        tr = [t for t in trades if t["owner"] == owner]
        d = done(tr)
        res[owner] = {"ideas": len(tr), "filled": len(d), "expired": sum(t["status"] == "expired" for t in tr),
                      **r_stats([t["r"] for t in d]), "monte_carlo": monte_carlo([t["r"] for t in d])}
    desk = [t for t in trades if t["owner"] == "desk"]
    days = sorted({t["day"] for t in desk})
    half = days[len(days) // 2] if days else None
    res["desk_by_setup"] = breakdown(desk, "setup")
    res["desk_by_hour_et"] = breakdown(desk, "hour_et")
    res["desk_halves"] = {"first": r_stats([t["r"] for t in done(desk) if half and t["day"] < half]),
                          "second": r_stats([t["r"] for t in done(desk) if half and t["day"] >= half])}
    return res


# ====================================================================== walk-forward
GRID = {"buy_score": [60, 68, 75], "max_stop_atr": [1.5, 2.5], "min_rvol": [1.2, 1.8]}


def walk_forward(snaps: List[Snap], days: List, cfg: dict, train: int = 20, test: int = 10, min_trades: int = 5) -> dict:
    by_day: Dict = {}
    for s in snaps:
        by_day.setdefault(s.now.astimezone(ET).date(), []).append(s)
    windows, oos, start = [], [], 0
    while start + train + test <= len(days):
        tr_days, te_days = days[start:start + train], days[start + train:start + train + test]
        tr_snaps = [x for d in tr_days for x in by_day.get(d, [])]
        te_snaps = [x for d in te_days for x in by_day.get(d, [])]
        best, best_score = None, -math.inf
        for vals in product(*GRID.values()):
            p = dict(zip(GRID, vals))
            c = {**cfg, **p}
            rs = [t["r"] for t in done(decide(tr_snaps, c)) if t["owner"] == "desk"]
            sc = sum(rs) if len(rs) >= min_trades else -math.inf
            if sc > best_score:
                best, best_score = p, sc
        if best is None:
            best = {k: cfg[k] for k in GRID}
        te = [t for t in decide(te_snaps, {**cfg, **best}) if t["owner"] == "desk"]
        oos += te
        windows.append({"train": f"{tr_days[0]}→{tr_days[-1]}", "test_period": f"{te_days[0]}→{te_days[-1]}",
                        "params": best, "train_total_r": None if best_score == -math.inf else round(best_score, 2),
                        "test_stats": r_stats([t["r"] for t in done(te)])})
        start += test
    stable = len({tuple(sorted(w["params"].items())) for w in windows}) <= max(1, len(windows) // 2)
    return {"windows": windows, "out_of_sample": r_stats([t["r"] for t in done(oos)]), "params_stable": stable}
