# -*- coding: utf-8 -*-
"""🔭 Scout — يراقب السوق ويختار الأسهم اللي فيها حركة واهتمام غير عادي.

المصدر: قوائم Alpaca (الأكثر نشاطاً + الأكثر ارتفاعاً) + قائمة أساسية من config، ثم لقطة IEX لكل سهم.
"""
from datetime import datetime
from typing import Dict, List, Tuple

from desk.alpaca import ET, Alpaca, parse_ts
from desk.bots.base import Opinion, pct, px


def universe(client: Alpaca, cfg: dict) -> Tuple[List[str], List[str]]:
    syms, notes = list(cfg.get("core_watchlist", [])), []
    gainers: List[str] = []
    for fn, label in ((lambda: client.most_actives(50, "volume"), "الأكثر تداولاً"),
                      (lambda: client.most_actives(50, "trades"), "الأكثر صفقات"),
                      (lambda: client.gainers(30), "الأكثر ارتفاعاً")):
        try:
            got = fn()
            syms += got
            if label == "الأكثر ارتفاعاً":
                gainers = got
        except Exception as e:  # noqa
            notes.append(f"تعذّر جلب قائمة {label}")
    clean = []
    for s in dict.fromkeys(syms):
        if s and s.replace(".", "").isalpha() and s not in cfg.get("exclude", []):
            clean.append(s)
    return clean, gainers + [f"!{n}" for n in notes]


def scan(snaps: Dict[str, dict], gainers: List[str], cfg: dict, now: datetime) -> List[Opinion]:
    et = now.astimezone(ET)
    frac = max(1, min(390, et.hour * 60 + et.minute - 570)) / 390
    out = []
    for sym, sn in snaps.items():
        db, pb, lt = sn.get("dailyBar") or {}, sn.get("prevDailyBar") or {}, sn.get("latestTrade") or {}
        price, prev = lt.get("p") or db.get("c"), pb.get("c")
        if not price or not prev:
            continue
        price, prev = float(price), float(prev)
        if not (cfg["min_price"] <= price <= cfg["max_price"]):
            continue
        chg = (price / prev - 1) * 100
        vol, pvol = float(db.get("v") or 0), float(pb.get("v") or 0)
        pace = vol / (pvol * frac) if pvol > 0 else None
        dollar = vol * float(db.get("vw") or price)
        if dollar < cfg["min_iex_dollar_volume"]:
            continue
        age = None
        if lt.get("t"):
            age = (now - parse_ts(lt["t"])).total_seconds()
        score = 40.0
        score += max(-25, min(25, chg * 3)) if chg > 0 else max(-30, chg * 4)
        if pace:
            score += min(25, (pace - 1) * 12)
        if sym in gainers:
            score += 8
        if chg > cfg["max_day_change_pct"]:
            score -= 20  # تحرّك كثيراً — غالباً متأخر
        score = max(0, min(100, score))
        stance = "positive" if score >= 60 else "neutral" if score >= 40 else "negative"
        pace_txt = f"وحجمه يمشي بوتيرة {pace:.1f}× أمس" if pace else "ووتيرة الحجم غير متوفرة"
        line = f"{sym} عند {px(price)} ({pct(chg)} اليوم) {pace_txt}" + (" — ومن قائمة الأكثر ارتفاعاً." if sym in gainers else ".")
        out.append(Opinion("scout", stance, round(score, 1), line, {
            "symbol": sym, "price": round(price, 4), "prev_close": prev, "change_pct": round(chg, 2),
            "volume_pace_x": round(pace, 2) if pace else None, "iex_dollar_volume": round(dollar),
            "in_gainers": sym in gainers, "last_trade_age_sec": round(age) if age is not None else None,
            "day_high": float(db.get("h") or price)}))
    out.sort(key=lambda o: o.score, reverse=True)
    return out
