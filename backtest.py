# -*- coding: utf-8 -*-
"""اختبار تاريخي كبير لمكتب التداول: النسخة 1 مقابل النسخة 2، وكل تعديل من النسخة 2 لحاله.

  python backtest.py               ← 300 يوم تداول على ~140 سهم (الهدف: 500+ صفقة للنسخة 1)
  python backtest.py --days 120    ← مدة أقصر

النتيجة: docs/backtest.json (يقرأه الموقع) + docs/backtest.md (تقرير مقروء).
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta

from desk import backtest as BT
from desk.alpaca import ET, UTC, Alpaca
from desk import ta

ROOT = os.path.dirname(os.path.abspath(__file__))
BT_DEBUG = []
DISCLOSURE = ("تنبيه: هذي محاكاة افتراضية على بيانات سابقة، ولا تمثّل أداء تداول فعلي ولا تضمن نتائج مستقبلية. "
              "النتائج تعتمد على جودة البيانات والانزلاق والسيولة وطريقة التنفيذ. للتعلّم والبحث فقط، وليست توصية.")
ABLATIONS = {  # كل تعديل من النسخة 2 لحاله فوق النسخة 1
    "no_chase": {"no_chase_r": 0.25},
    "breakeven": {"breakeven_at_r": 1.0},
    "require_breakout": {"require_breakout": True},
    "cutoff_1430": {"no_entries_after_min": 870},
    "min_stop_0.3%": {"min_stop_pct": 0.3},
    "wide_stop_ok": {"max_stop_atr": 3.0},
}
NAMES = {"no_chase": "بدون مطاردة", "breakeven": "تعادل عند +1R", "require_breakout": "الارتداد لازم يخترق",
         "cutoff_1430": "لا دخول بعد 2:30", "min_stop_0.3%": "ستوب ≥ 0.3%", "wide_stop_ok": "ستوب واسع مقبول (3 ATR)"}


def arg(name, default):
    return type(default)(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else default


def load_profiles():
    base = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
    v2 = dict(base)
    v2.update({k: v for k, v in base.get("profiles", {}).get("v2", {}).items() if not k.startswith("_")})
    return base, v2


def main():
    t0 = time.time()
    v1, v2 = load_profiles()
    key, sec = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_API_SECRET")
    if not key or not sec:
        sys.exit("ضيف ALPACA_API_KEY و ALPACA_API_SECRET.")
    n_days = arg("--days", 300)
    c = Alpaca(key, sec)
    symbols = list(dict.fromkeys(s for s in v1.get("backtest_universe", v1["core_watchlist"]) if s not in v1["exclude"]))
    end = datetime.now(UTC) - timedelta(minutes=20)
    start = end - timedelta(days=int(n_days * 1.47) + 25)
    print(f"جلب شموع 5 دقائق لـ {len(symbols)} سهم من {start:%Y-%m-%d} …", flush=True)
    feed, bars = "sip", {}
    allsyms = ["SPY", "QQQ"] + symbols

    def fetch(fd):
        out = {}
        for i in range(0, len(allsyms), 5):
            part = allsyms[i:i + 5]
            got = c.bars(part, "5Min", start, end, feed=fd)
            for k, v in got.items():
                if len(v):
                    s = ta.regular_session(v)          # نحتفظ بالجلسة النظامية بس (أخف على الذاكرة)
                    s.index = s.index.tz_convert(UTC)
                    out[k] = s
            BT_DEBUG.append(f"{fd} " + ", ".join(f"{k}={len(v)}" for k, v in got.items()))
            print(f"  {min(i + 5, len(allsyms))}/{len(allsyms)} ({c.calls} طلب)", flush=True)
        return out
    try:
        bars = fetch("sip")
    except Exception as e:
        BT_DEBUG.append(f"sip error: {e}")
    if "SPY" not in bars or len(bars) < len(allsyms) * 0.7:
        feed, bars = "iex", fetch("iex")
    spy = bars.get("SPY")
    if spy is None or spy.empty:
        sys.exit("ما وصلت بيانات SPY — ما أقدر أحدد أيام التداول.")
    all_days = sorted(set(spy.index.tz_convert(ET).date))
    days = [d for d in all_days if d < end.astimezone(ET).date()][-n_days:]
    print(f"أيام الاختبار: {len(days)} ({days[0]} → {days[-1]}) • feed={feed} • أسهم فيها بيانات: {len(bars) - 2}", flush=True)
    try:
        items = c.news_range(symbols, datetime.combine(days[0], datetime.min.time(), tzinfo=ET) - timedelta(days=1), end,
                             max_pages=1500)
        print(f"أخبار: {len(items)}", flush=True)
    except Exception as e:
        print(f"الأخبار غير متوفرة: {e}", flush=True)
        items = None

    t1 = time.time()
    snaps = BT.collect(bars, items, [s for s in symbols if s in bars], days, v1, log=lambda m: print(m, flush=True))
    print(f"التحضير خلص في {time.time() - t1:.0f} ث", flush=True)

    tr1, tr2 = BT.decide(snaps, v1), BT.decide(snaps, v2)
    ablation = {}
    base_stats = BT.r_stats([t["r"] for t in BT.done(tr1) if t["owner"] == "desk"])
    for name, over in ABLATIONS.items():
        st = BT.r_stats([t["r"] for t in BT.done(BT.decide(snaps, {**v1, **over})) if t["owner"] == "desk"])
        ablation[name] = {"label": NAMES[name], **st, "delta_total_r": round(st.get("total_r", 0) - base_stats.get("total_r", 0), 2)}
    print("walk-forward …", flush=True)
    wf1 = BT.walk_forward(snaps, days, v1)
    wf2 = BT.walk_forward(snaps, days, v2)
    out = {"generated_utc": datetime.now(UTC).isoformat(), "period": [str(days[0]), str(days[-1])], "days": len(days),
           "symbols": [s for s in symbols if s in bars], "feed": feed, "snaps": len(snaps),
           "versions": {"v1": {"label": "النسخة 1 (الحالية)", "summary": BT.summarize(tr1), "walk_forward": wf1},
                        "v2": {"label": "النسخة 2", "summary": BT.summarize(tr2), "walk_forward": wf2,
                               "changes": {k: v for k, v in v2.get("profiles", {}).get("v2", {}).items()}}},
           "ablation": ablation, "grid": BT.GRID, "api_calls": c.calls, "seconds": round(time.time() - t0),
           "limits": ["Sentiment وFinnhub وSEC وReddit غير متوفرة تاريخياً — مستبعدة",
                      "قائمة ~140 سهم موجودة اليوم (تحيّز بقاء)، وScout يختار المتحركين منها لحظياً",
                      "تنفيذ افتراضي: الدخول عند الاختراق مع انزلاق، ولمس الستوب والهدف بنفس الشمعة = خسارة"],
           "disclosure": DISCLOSURE,
           "trades_v1": [t for t in tr1 if t["owner"] == "desk"][-1500:],
           "trades_v2": [t for t in tr2 if t["owner"] == "desk"][-1500:]}
    # توافق مع واجهة الموقع القديمة
    out["summary"], out["walk_forward"] = out["versions"]["v1"]["summary"], out["versions"]["v1"]["walk_forward"]
    os.makedirs(os.path.join(ROOT, "docs"), exist_ok=True)
    json.dump(out, open(os.path.join(ROOT, "docs", "backtest.json"), "w", encoding="utf-8"), ensure_ascii=False, default=str)
    md = report_md(out)
    open(os.path.join(ROOT, "docs", "backtest.md"), "w", encoding="utf-8").write(md)
    print(md, flush=True)


def _row(name, s):
    if not s.get("trades"):
        return f"| {name} | 0 | — | — | — | — | — | — |"
    return (f"| {name} | {s['trades']} | {s['win_rate']}% | {s['avg_r']} | {s['total_r']} | "
            f"{s['profit_factor'] if s['profit_factor'] is not None else '—'} | {s['max_dd_r']} | {s['max_losing_streak']} |")


HEAD = ["| | صفقات | فوز | متوسط R | مجموع R | Profit factor | أقصى تراجع (R) | أطول سلسلة خسائر |",
        "|---|---|---|---|---|---|---|---|"]


def report_md(o: dict) -> str:
    V = o["versions"]
    L = [f"# اختبار تاريخي — مكتب التداول", "",
         f"الفترة: {o['period'][0]} → {o['period'][1]} ({o['days']} يوم تداول) • الأسهم: {len(o['symbols'])} • البيانات: {o['feed']} • لحظات مرشّحة: {o['snaps']}", "",
         "## النسخة 1 مقابل النسخة 2 (صفقات المكتب)", ""] + HEAD
    for k in ("v1", "v2"):
        L.append(_row(V[k]["label"], V[k]["summary"]["desk"]))
    L += ["", "## كل تعديل من النسخة 2 لحاله (فوق النسخة 1)", ""] + HEAD
    for k, a in o["ablation"].items():
        L.append(_row(f"{a['label']} ({a['delta_total_r']:+}R)", a))
    for k in ("v1", "v2"):
        S, W = V[k]["summary"], V[k]["walk_forward"]
        mc = S["desk"].get("monte_carlo") or {}
        L += ["", f"## {V[k]['label']}", ""] + HEAD + [_row("🏛️ المكتب (شراء)", S["desk"]), _row("🟡 لو دخلنا «انتظار»", S["wait"]),
                                                     _row("🛡️ اللي أوقفها Skeptic", S["blocked"]), ""]
        if S["desk"].get("trades", 0) < 30:
            L.append("⚠️ أقل من 30 صفقة — النتيجة تُعتبر صدفة.\n")
        if mc:
            L.append(f"**Monte Carlo:** احتمال المجموع سالب {mc['prob_negative_total']}% • أسوأ 5% تراجع {mc['worst5pct_max_dd_r']}R • "
                     f"مجموع R بين {mc['total_r_p5']} و {mc['total_r_p95']}\n")
        L.append("**الأرباع:** " + " • ".join(f"ربع {q}: {s.get('total_r', 0)}R ({s.get('trades', 0)})" for q, s in S.get("desk_quarters", {}).items()) + "\n")
        L.append(f"**Walk-forward (60 يوم اختيار → 20 يوم حكم):** خارج العيّنة {W['out_of_sample'].get('trades', 0)} صفقة، "
                 f"{W['out_of_sample'].get('total_r', 0)}R • نوافذ رابحة {W['positive_windows']}/{W['n_windows']} • "
                 f"{'الإعدادات ثابتة نسبياً ✅' if W['params_stable'] else 'الإعدادات تتغير كثير ⚠️'}\n")
        L.append("حسب الإعداد: " + " • ".join(f"{k2}: {v.get('trades')} صفقة {v.get('total_r')}R" for k2, v in S["desk_by_setup"].items()))
        L.append("حسب الساعة (نيويورك): " + " • ".join(f"{k2}: {v.get('total_r')}R/{v.get('trades')}" for k2, v in S["desk_by_hour_et"].items()))
        L.append("حسب حجم الستوب: " + " • ".join(f"{k2[2:]}: {v.get('total_r')}R/{v.get('trades')}" for k2, v in S["desk_by_stop"].items()))
    L += ["", "## حدود", ""] + [f"- {x}" for x in o["limits"]] + ["", f"> {o['disclosure']}"]
    return "\n".join(L)


if __name__ == "__main__":
    import traceback
    try:
        main()
    except BaseException as e:   # نحفظ السبب في المستودع عشان نقدر نقرأه
        msg = traceback.format_exc() if not isinstance(e, SystemExit) else f"SystemExit: {e}"
        os.makedirs(os.path.join(ROOT, "docs"), exist_ok=True)
        open(os.path.join(ROOT, "docs", "backtest_error.txt"), "w", encoding="utf-8").write(
            datetime.now(UTC).isoformat() + "\n" + msg + "\n\n" + "\n".join(BT_DEBUG))
        print(msg)
        raise
