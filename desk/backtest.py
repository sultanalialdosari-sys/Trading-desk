# -*- coding: utf-8 -*-
"""اختبار تاريخي لمكتب التداول — نعيد تشغيل نفس البوتات على الماضي، لحظة بلحظة.

مبادئ (من سكيل backtesting-frameworks):
 • بدون نظر للمستقبل: كل «فحص» يشوف فقط الشموع اللي انقفلت قبل لحظته، والأخبار المنشورة قبلها.
   (المؤشرات تُحسب مرة وحدة على كل التاريخ، وكلها سببية: VWAP و EMA و RSI و ATR و RVOL ما تستخدم إلا الماضي.)
 • Scout يختار «المتحركين» لحظياً من قائمة كبيرة (مثل المكتب الحي)، مو قائمة ثابتة صغيرة.
 • الدخول فقط عند اختراق سعر الدخول في شمعة لاحقة، والمحاكاة متحفظة (لمس الستوب والهدف بشمعة = خسارة).
 • نفس اللحظات تُقيَّم بأكثر من نسخة إعدادات (v1 / v2 / كل تعديل لحاله) — مقارنة عادلة.
 • walk-forward: نختار الإعدادات على فترة، ونحكم عليها على الفترة اللي بعدها فقط.

حدود معروفة: Sentiment غير متوفر تاريخياً، وFinnhub/SEC/Reddit لحظية فقط، والقائمة أسهم موجودة اليوم (تحيّز بقاء).
"""
import bisect
import math
import os
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from itertools import product
from multiprocessing import get_context
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from desk import ta
from desk.alpaca import ET, UTC, parse_ts
from desk.bots import charts, chief, news, scout, skeptic
from desk.bots.base import Opinion
from desk.ledger import _close, step_trade

SCAN_EVERY_MIN = 15


@dataclass
class Snap:
    now: datetime
    sym: str
    so: Opinion
    ch: Opinion
    nw: Optional[Opinion]
    mkt: dict
    day_bars: Optional[pd.DataFrame]


def scan_times(day) -> List[datetime]:
    t = datetime.combine(day, time(9, 45), tzinfo=ET)
    end = datetime.combine(day, time(15, 45), tzinfo=ET)
    out = []
    while t <= end:
        out.append((t + timedelta(minutes=1)).astimezone(UTC))
        t += timedelta(minutes=SCAN_EVERY_MIN)
    return out


# ====================================================================== التحضير (لكل سهم، بالتوازي)
_G: dict = {}


def _prep_symbol(sym: str):
    """يرجع [(now, scout_op, charts_op)] لكل لحظة فحص يكون فيها السهم مؤهّل عند Scout."""
    raw, days, cfg = _G["bars"].get(sym), _G["days"], _G["cfg"]
    if raw is None or raw.empty:
        return sym, []
    sess = ta.regular_session(raw)
    if len(sess) < 300:
        return sym, []
    enr = ta.enrich(sess)
    rv = ta.rvol_series(sess)
    idx = enr.index
    dates = np.array(idx.date)
    close, vol, vwap = enr["close"].values, enr["volume"].values, enr["vwap"].values
    high = enr["high"].values
    out = []
    day_set = set(days)
    uniq = sorted(set(dates))
    prev_of = {d: uniq[i - 1] for i, d in enumerate(uniq) if i}
    for day in uniq:
        if day not in day_set or day not in prev_of:
            continue
        lo = np.searchsorted(dates, day, side="left")
        hi = np.searchsorted(dates, day, side="right")
        plo = np.searchsorted(dates, prev_of[day], side="left")
        prev_close, prev_vol = close[lo - 1], vol[plo:lo].sum()
        for now in scan_times(day):
            cut = pd.Timestamp(now).tz_convert(ET) - pd.Timedelta(minutes=5)
            k = idx.searchsorted(cut, side="right") - 1          # آخر شمعة مقفلة قبل اللحظة
            if k < lo + 2 or k >= hi:
                continue
            snap = {sym: {"latestTrade": {"p": float(close[k]), "t": now.strftime("%Y-%m-%dT%H:%M:%SZ")},
                          "dailyBar": {"c": float(close[k]), "h": float(high[lo:k + 1].max()),
                                       "v": float(vol[lo:k + 1].sum()), "vw": float(vwap[k])},
                          "prevDailyBar": {"c": float(prev_close), "v": float(prev_vol)}}}
            got = scout.scan(snap, [], cfg, now)
            if not got:
                continue
            ch = charts.plan_at(sym, enr.iloc[max(0, k - 240):k + 1], rv[k], cfg)
            out.append((now, got[0], ch))
    return sym, out


def _market_by_time(bars: Dict[str, pd.DataFrame], days) -> Dict[datetime, dict]:
    out: Dict[datetime, dict] = {}
    prep = {}
    for s in ("SPY", "QQQ"):
        if s in bars and not bars[s].empty:
            sess = ta.regular_session(bars[s])
            prep[s] = (sess, ta.vwap(sess).values, np.array(sess.index.date))
    for day in days:
        for now in scan_times(day):
            m, res = {"score": None}, {}
            for s, (sess, vw, dates) in prep.items():
                k = sess.index.searchsorted(pd.Timestamp(now).tz_convert(ET) - pd.Timedelta(minutes=5), side="right") - 1
                if k < 0 or dates[k] != day:
                    continue
                lo = np.searchsorted(dates, day)
                c = float(sess["close"].iloc[k])
                res[s] = {"close": round(c, 2), "vwap": round(float(vw[k]), 2),
                          "chg_open_pct": round((c / float(sess["open"].iloc[lo]) - 1) * 100, 2)}
                m[f"{s.lower()}_below_vwap"] = c < vw[k]
            if res:
                above = sum(1 for s in res if not m[f"{s.lower()}_below_vwap"])
                m["score"] = {0: 25, 1: 50, 2: 75}[above] if len(res) == 2 else (70 if above else 30)
            m["detail"] = res
            out[now] = m
    return out


def collect(bars: Dict[str, pd.DataFrame], news_items: Optional[list], symbols: List[str], days: List,
            cfg: dict, log=print, workers: Optional[int] = None) -> List[Snap]:
    """المرحلة الثقيلة: آراء Scout/Charts/News لكل لحظة فحص — لا تعتمد على عتبات القرار."""
    _G.update(bars=bars, days=days, cfg=cfg)
    workers = workers or max(1, min(8, os.cpu_count() or 1))
    per_time: Dict[datetime, list] = {}
    if workers > 1:
        with get_context("fork").Pool(workers) as pool:
            results = pool.imap_unordered(_prep_symbol, symbols, chunksize=1)
            for i, (sym, rows) in enumerate(results, 1):
                for now, so, ch in rows:
                    per_time.setdefault(now, []).append((so, ch))
                if i % 10 == 0 or i == len(symbols):
                    log(f"  {i}/{len(symbols)} سهم جاهز")
    else:
        for sym in symbols:
            for now, so, ch in _prep_symbol(sym)[1]:
                per_time.setdefault(now, []).append((so, ch))
    mkt = _market_by_time(bars, days)

    # فهرس الأخبار لكل سهم (للبحث السريع بالوقت)
    nidx: Dict[str, tuple] = {}
    if news_items is not None:
        tmp: Dict[str, list] = {}
        for n in news_items:
            if not n.get("created_at"):
                continue
            t = parse_ts(n["created_at"])
            for s in n.get("symbols") or []:
                tmp.setdefault(s, []).append((t, n))
        for s, lst in tmp.items():
            lst.sort(key=lambda x: x[0])
            nidx[s] = ([x[0] for x in lst], [x[1] for x in lst])

    # أيام كل سهم للمحاكاة (جلسة نظامية، توقيت UTC)
    day_cache: Dict[tuple, pd.DataFrame] = {}

    def day_bars(sym, day):
        key = (sym, day)
        if key not in day_cache:
            raw = bars.get(sym)
            s = ta.regular_session(raw[(raw.index >= pd.Timestamp(datetime.combine(day, time(0), tzinfo=ET))) &
                                       (raw.index < pd.Timestamp(datetime.combine(day + timedelta(days=1), time(0), tzinfo=ET)))])
            s.index = s.index.tz_convert(UTC)
            day_cache[key] = s
        return day_cache[key]

    snaps: List[Snap] = []
    for now in sorted(per_time):
        cands = sorted(per_time[now], key=lambda x: x[0].score, reverse=True)[:cfg["scout_top"]]
        deep = [(so, ch) for so, ch in cands if "entry" in ch.facts or (ch.score or 0) >= 60][:cfg["deep_max"]]
        for so, ch in deep:
            s = so.facts["symbol"]
            if news_items is None:
                nw = news.analyze(s, None, now)
            else:
                ts, items = nidx.get(s, ([], []))
                lo, hi = bisect.bisect_left(ts, now - timedelta(hours=24)), bisect.bisect_right(ts, now)
                nw = news.analyze(s, list(reversed(items[lo:hi])), now)
            snaps.append(Snap(now, s, so, ch, nw, mkt.get(now, {"score": None}), day_bars(s, now.astimezone(ET).date())))
    log(f"  لحظات مرشّحة: {len(snaps)}")
    return snaps


# ====================================================================== القرار + المحاكاة
NO_SENTI = Opinion("sentiment", "unavailable", None, "غير متوفر تاريخياً", {})


def decide(snaps: List[Snap], cfg: dict) -> List[dict]:
    """Skeptic + Chief بالإعدادات المعطاة، ثم المحاكاة. ترجع قائمة الصفقات (desk / wait / blocked)."""
    trades, last = [], {}
    for sn in snaps:
        ch = sn.ch
        if "entry" in ch.facts and ch.facts.get("rr") != cfg["target_r"]:
            ch = Opinion(ch.bot, ch.stance, ch.score, ch.line, charts.retarget(ch.facts, cfg["target_r"]))
        sk = skeptic.review(sn.sym, sn.so, ch, sn.nw, NO_SENTI, sn.mkt, sn.now, cfg)
        ops = {"scout": sn.so, "charts": ch, "news": sn.nw, "sentiment": NO_SENTI, "skeptic": sk}
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
              "score": cf.score, "setup": ch.facts.get("setup"), "hour_et": sn.now.astimezone(ET).hour,
              "day": sn.now.astimezone(ET).date().isoformat(),
              "risk_pct": round((t["entry"] - t["stop"]) / t["entry"] * 100, 3),
              "objections": [o["text"][:60] for o in sk.facts["objections"] if o["severity"] != "minor"][:2]}
        db = sn.day_bars
        if db is not None and not db.empty:
            step_trade(tr, db, cfg["slippage_pct"], cfg)
            if tr["status"] == "pending":
                tr["status"] = "expired"
            elif tr["status"] == "open":
                _close(tr, float(db["close"].iloc[-1]), db.index[-1].isoformat(), cfg["slippage_pct"], "closed")
        else:
            tr["status"] = "expired"
        trades.append(tr)
    return trades


# ====================================================================== المقاييس
DONE = ("won", "lost", "closed", "scratch")


def done(trades):
    return [t for t in trades if t["status"] in DONE and t.get("r") is not None]


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


def breakdown(trades: List[dict], key) -> dict:
    out = {}
    for t in done(trades):
        k = key(t) if callable(key) else t.get(key)
        out.setdefault(str(k), []).append(t["r"])
    return {k: r_stats(v) for k, v in sorted(out.items())}


def stop_bucket(t):
    r = t.get("risk_pct") or 0
    return "a <0.25%" if r < 0.25 else "b 0.25-0.5%" if r < 0.5 else "c 0.5-1%" if r < 1 else "d >1%"


def summarize(trades: List[dict]) -> dict:
    res = {}
    for owner in ("desk", "wait", "blocked"):
        tr = [t for t in trades if t["owner"] == owner]
        d = done(tr)
        res[owner] = {"ideas": len(tr), "filled": len(d), "expired": sum(t["status"] == "expired" for t in tr),
                      "scratch": sum(t["status"] == "scratch" for t in tr),
                      **r_stats([t["r"] for t in d]), "monte_carlo": monte_carlo([t["r"] for t in d])}
    desk = [t for t in trades if t["owner"] == "desk"]
    days = sorted({t["day"] for t in desk})
    res["desk_by_setup"] = breakdown(desk, "setup")
    res["desk_by_hour_et"] = breakdown(desk, "hour_et")
    res["desk_by_stop"] = breakdown(desk, stop_bucket)
    res["desk_by_month"] = breakdown(desk, lambda t: t["day"][:7])
    q = [days[int(len(days) * i / 4)] for i in range(1, 4)] if len(days) >= 4 else []
    res["desk_quarters"] = breakdown(desk, lambda t: sum(t["day"] >= x for x in q) + 1) if q else {}
    return res


# ====================================================================== walk-forward
GRID = {"buy_score": [60, 68, 75], "target_r": [1.5, 2.0], "breakeven_at_r": [None, 1.0]}


def walk_forward(snaps: List[Snap], days: List, cfg: dict, train: int = 60, test: int = 20, min_trades: int = 10,
                 grid: dict = None) -> dict:
    grid = grid or GRID
    by_day: Dict = {}
    for s in snaps:
        by_day.setdefault(s.now.astimezone(ET).date(), []).append(s)
    windows, oos, start = [], [], 0
    while start + train + test <= len(days):
        tr_days, te_days = days[start:start + train], days[start + train:start + train + test]
        tr_snaps = [x for d in tr_days for x in by_day.get(d, [])]
        te_snaps = [x for d in te_days for x in by_day.get(d, [])]
        best, best_score = None, -math.inf
        for vals in product(*grid.values()):
            p = dict(zip(grid, vals))
            rs = [t["r"] for t in done(decide(tr_snaps, {**cfg, **p})) if t["owner"] == "desk"]
            sc = sum(rs) if len(rs) >= min_trades else -math.inf
            if sc > best_score:
                best, best_score = p, sc
        if best is None:
            best = {k: cfg.get(k) for k in grid}
        te = [t for t in decide(te_snaps, {**cfg, **best}) if t["owner"] == "desk"]
        oos += te
        windows.append({"train": f"{tr_days[0]}→{tr_days[-1]}", "test_period": f"{te_days[0]}→{te_days[-1]}",
                        "params": best, "train_total_r": None if best_score == -math.inf else round(best_score, 2),
                        "test_stats": r_stats([t["r"] for t in done(te)])})
        start += test
    stable = len({tuple(sorted(map(str, w["params"].items()))) for w in windows}) <= max(1, len(windows) // 2)
    pos = sum(1 for w in windows if w["test_stats"].get("total_r", 0) > 0)
    return {"windows": windows, "out_of_sample": r_stats([t["r"] for t in done(oos)]), "params_stable": stable,
            "positive_windows": pos, "n_windows": len(windows)}
