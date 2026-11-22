# -*- coding: utf-8 -*-
"""🛡️ Skeptic — يعارض كل صفقة. وظيفته يدوّر سبب يمنعك، مو سبب يقنعك.

كل اعتراض له شدّة:
 • veto  — يوقف الصفقة نهائياً (BLOCKED BY SKEPTIC)
 • major — يخصم 15 نقطة
 • minor — يخصم 5 نقاط
"""
from datetime import datetime
from typing import List, Tuple

from desk.alpaca import ET
from desk.bots.base import Opinion, px


def review(sym: str, scout: Opinion, charts: Opinion, news: Opinion, senti: Opinion, market: dict,
           now: datetime, cfg: dict) -> Opinion:
    obj: List[Tuple[str, str]] = []
    c = charts.facts
    et = now.astimezone(ET)
    hm = et.hour * 60 + et.minute

    if "entry" not in c:
        obj.append(("veto", "ما فيه خطة دخول أصلاً — ما نشتري بدون نقطة دخول وستوب."))
    else:
        entry, stop, target, a = c["entry"], c["stop"], c["target"], c["atr"]
        risk = entry - stop
        if risk <= 0:
            obj.append(("veto", "الستوب فوق الدخول — الخطة مكسورة."))
        else:
            stop_atr = risk / a
            if stop_atr > cfg["max_stop_atr"]:
                obj.append(("major", f"الستوب بعيد ({stop_atr:.1f}× ATR) — الخسارة لو غلطنا كبيرة على الحركة المعتادة."))
            if stop_atr < 0.35:
                obj.append(("major", f"الستوب ضيق جداً ({stop_atr:.2f}× ATR) — أي ذبذبة عادية بتطلّعك."))
            # الستوب في المكان اللي يحطه الكل: تحت VWAP مباشرة أو تحت رقم دائري
            near_vwap = abs(stop - c["vwap"]) <= 0.15 * a
            r_unit = 1 if entry >= 20 else 0.5 if entry >= 5 else 0.1
            round_below = (stop % r_unit) >= r_unit * 0.9 or (stop % r_unit) <= r_unit * 0.02
            if near_vwap or round_below:
                where = "تحت VWAP مباشرة" if near_vwap else "عند رقم دائري"
                obj.append(("minor", f"ستوبك {px(stop)} {where} — نفس المكان اللي يحط فيه الكل ستوباتهم، وغالباً ينضرب قبل الصعود."))
            nr = c.get("next_resistance")
            if nr and nr < target:
                room = (nr - entry) / risk
                sev = "veto" if room < 1.0 else "major" if room < cfg["min_rr"] else None
                if sev:
                    obj.append((sev, f"فيه مقاومة عند {px(nr)} قبل الهدف — العائد الحقيقي للمخاطرة {room:.1f} إلى 1 بس."))
        if c.get("extension_atr", 0) > 2:
            obj.append(("major", f"السعر بعيد عن VWAP بـ{c['extension_atr']:.1f}× ATR — هذي مطاردة، مو دخول."))
        if c.get("rsi") and c["rsi"] > 75:
            obj.append(("major", f"RSI {c['rsi']} — تشبّع شرائي."))
        rv = c.get("rvol")
        if rv is None:
            obj.append(("minor", "RVOL غير متوفر — ما أقدر أتأكد إن فيه اهتمام فعلي."))
        elif rv < cfg["min_rvol"]:
            obj.append(("major", f"RVOL {rv}× بس — ما فيه اهتمام كافي يدفع السعر."))

    if news.stance == "negative":
        obj.append(("veto" if news.score is not None and news.score <= 15 else "major", "الأخبار ضدنا: " + news.line))
    if news.facts.get("count") == 0 and (scout.facts.get("change_pct") or 0) > 8:
        obj.append(("minor", "طالع أكثر من 8% بدون أي خبر — ممكن ضخ مؤقت."))
    if senti.facts.get("bullish_pct", 0) >= 85:
        obj.append(("minor", f"{senti.facts['bullish_pct']}% متفائلين — لما الكل يشتري، مين بقي يشتري بعدك؟"))
    if senti.stance == "negative":
        obj.append(("minor", "المزاج العام سلبي."))
    if market.get("spy_below_vwap") and market.get("qqq_below_vwap"):
        obj.append(("major", "السوق العام (SPY و QQQ) تحت VWAP — الموجة ضد الشراء."))
    age = scout.facts.get("last_trade_age_sec")
    if age is not None and age > cfg["max_price_age_sec"]:
        obj.append(("veto", f"آخر سعر عمره {age // 60} دقيقة — البيانات قديمة، ما نقرّر عليها."))
    if hm >= cfg["no_entries_after_min"]:
        obj.append(("veto", "متأخرين في الجلسة — ما فيه وقت كافي للصفقة تشتغل."))
    elif hm < 570 + cfg["skip_first_min"]:
        obj.append(("minor", "أول دقائق الافتتاح — الحركة عشوائية."))

    vetoes = [t for s, t in obj if s == "veto"]
    penalty = sum(15 if s == "major" else 5 if s == "minor" else 0 for s, _ in obj)
    if vetoes:
        line = "ضد. " + vetoes[0]
        stance = "negative"
    elif obj:
        main = sorted(obj, key=lambda x: x[0] != "major")[0][1]
        line = ("ضد. " if penalty >= 15 else "متحفظ. ") + main
        stance = "negative" if penalty >= 15 else "neutral"
    else:
        line = "دوّرت سبب أرفض فيه وما لقيت — الستوب منطقي والعائد يستاهل المخاطرة."
        stance = "positive"
    return Opinion("skeptic", stance, None, line, {"symbol": sym, "objections": [{"severity": s, "text": t} for s, t in obj],
                                                  "veto": bool(vetoes), "penalty": penalty})
