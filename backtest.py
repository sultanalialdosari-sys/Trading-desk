# -*- coding: utf-8 -*-
"""اختبار تاريخي لمكتب التداول.

  python backtest.py              ← آخر 60 يوم تداول
  python backtest.py --days 40    ← مدة أقصر

النتيجة: docs/backtest.json (يقرأه الموقع) + docs/backtest.md (تقرير مقروء).
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta

from desk import backtest as BT
from desk.alpaca import ET, UTC, Alpaca, ApiError

ROOT = os.path.dirname(os.path.abspath(__file__))
BT_DEBUG = []
DISCLOSURE = ("تنبيه: هذي محاكاة افتراضية على بيانات سابقة، ولا تمثّل أداء تداول فعلي ولا تضمن نتائج مستقبلية. "
              "النتائج تعتمد على جودة البيانات والانزلاق والسيولة وطريقة التنفيذ. للتعلّم والبحث فقط، وليست توصية.")


def arg(name, default):
    return type(default)(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else default


def main():
    t0 = time.time()
    cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
    key, sec = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_API_SECRET")
    if not key or not sec:
        sys.exit("ضيف ALPACA_API_KEY و ALPACA_API_SECRET.")
    n_days = arg("--days", 60)
    c = Alpaca(key, sec)
    symbols = [s for s in cfg["core_watchlist"] + cfg.get("backtest_extra", []) if s not in cfg["exclude"]]
    symbols = list(dict.fromkeys(symbols))
    end = datetime.now(UTC) - timedelta(minutes=20)          # البيانات الكاملة (SIP) مجانية إذا عمرها > 15 دقيقة
    start = end - timedelta(days=int(n_days * 1.5) + 20)       # + أيام تسخين للمؤشرات و RVOL
    print(f"جلب شموع 5 دقائق لـ {len(symbols)} سهم من {start:%Y-%m-%d} …")
    feed, bars, debug = "sip", {}, []
    allsyms = symbols + ["SPY", "QQQ"]

    def fetch(fd):
        out = {}
        for i in range(0, len(allsyms), 10):
            part = allsyms[i:i + 10]
            got = c.bars(part, "5Min", start, end, feed=fd)
            debug.append(f"{fd} {part[0]}..{part[-1]}: " + ", ".join(f"{k}={len(v)}" for k, v in got.items()) or "فاضي")
            out.update({k: v for k, v in got.items() if len(v)})
        return out
    try:
        bars = fetch("sip")
    except Exception as e:
        debug.append(f"sip error: {e}")
    if "SPY" not in bars:
        try:
            feed, bars = "iex", fetch("iex")
        except Exception as e:
            debug.append(f"iex error: {e}")
    BT_DEBUG.extend(debug)
    spy = bars.get("SPY")
    if spy is None or spy.empty:
        sys.exit("ما وصلت بيانات SPY — ما أقدر أحدد أيام التداول.")
    all_days = sorted(set(spy.index.tz_convert(ET).date))
    days = [d for d in all_days if d < end.astimezone(ET).date()][-n_days:]
    print(f"أيام الاختبار: {len(days)} ({days[0]} → {days[-1]}) • feed={feed}")
    try:
        items = c.news_range(symbols, datetime.combine(days[0], datetime.min.time(), tzinfo=ET) - timedelta(days=1), end)
        print(f"أخبار: {len(items)}")
    except Exception as e:
        print(f"الأخبار غير متوفرة: {e}")
        items = None

    snaps = BT.collect(bars, items, symbols, days, cfg)
    trades = BT.decide(snaps, cfg)
    summary = BT.summarize(trades)
    wf = BT.walk_forward(snaps, days, cfg)
    out = {"generated_utc": datetime.now(UTC).isoformat(), "period": [str(days[0]), str(days[-1])], "days": len(days),
           "symbols": symbols, "feed": feed, "config": {k: cfg[k] for k in ("buy_score", "wait_score", "max_stop_atr", "min_rvol", "target_r", "valid_min", "slippage_pct")},
           "summary": summary, "walk_forward": wf, "grid": BT.GRID, "api_calls": c.calls, "seconds": round(time.time() - t0),
           "limits": ["Sentiment غير متوفر تاريخياً — مستبعد ووزنه موزّع",
                      "قائمة أسهم ثابتة (core_watchlist) بدل قوائم الأكثر نشاطاً اللحظية — تحيّز اختيار وبقاء",
                      "تنفيذ افتراضي: الدخول عند الاختراق مع انزلاق، ولمس الستوب والهدف بنفس الشمعة = خسارة"],
           "disclosure": DISCLOSURE,
           "trades": trades[-400:]}
    os.makedirs(os.path.join(ROOT, "docs"), exist_ok=True)
    json.dump(out, open(os.path.join(ROOT, "docs", "backtest.json"), "w", encoding="utf-8"), ensure_ascii=False, default=str)
    open(os.path.join(ROOT, "docs", "backtest.md"), "w", encoding="utf-8").write(report_md(out))
    print(report_md(out))


def _row(name, s):
    if not s.get("trades"):
        return f"| {name} | 0 | — | — | — | — | — | — |"
    return (f"| {name} | {s['trades']} | {s['win_rate']}% | {s['avg_r']} | {s['total_r']} | "
            f"{s['profit_factor'] if s['profit_factor'] is not None else '—'} | {s['max_dd_r']} | {s['max_losing_streak']} |")


def report_md(o: dict) -> str:
    S = o["summary"]
    L = [f"# اختبار تاريخي — مكتب التداول", "",
         f"الفترة: {o['period'][0]} → {o['period'][1]} ({o['days']} يوم تداول) • الأسهم: {len(o['symbols'])} • البيانات: {o['feed']}", "",
         "| المحفظة | صفقات | فوز | متوسط R | مجموع R | Profit factor | أقصى تراجع (R) | أطول سلسلة خسائر |",
         "|---|---|---|---|---|---|---|---|",
         _row("🏛️ المكتب (شراء)", S["desk"]), _row("🟡 لو دخلنا «انتظار»", S["wait"]), _row("🛡️ اللي أوقفها Skeptic", S["blocked"]), ""]
    mc = S["desk"].get("monte_carlo") or {}
    if mc:
        L += [f"**Monte Carlo (2000 محاكاة):** احتمال يكون المجموع سالب {mc['prob_negative_total']}% • "
              f"أسوأ 5% تراجع {mc['worst5pct_max_dd_r']}R • مجموع R بين {mc['total_r_p5']} و {mc['total_r_p95']}", ""]
    h = S["desk_halves"]
    L += [f"**الثبات:** النصف الأول {h['first'].get('total_r', 0)}R ({h['first'].get('trades', 0)} صفقة) • "
          f"النصف الثاني {h['second'].get('total_r', 0)}R ({h['second'].get('trades', 0)} صفقة)", ""]
    wf = o["walk_forward"]
    L += ["## Walk-forward (اختيار الإعدادات على 20 يوم، والحكم على الـ10 اللي بعدها)", "",
          f"خارج العيّنة: {wf['out_of_sample'].get('trades', 0)} صفقة، مجموع {wf['out_of_sample'].get('total_r', 0)}R • "
          f"الإعدادات {'ثابتة نسبياً ✅' if wf['params_stable'] else 'تتغير كثير ⚠️ (غالباً الميزة ضعيفة)'}", ""]
    for w in wf["windows"]:
        ts = w["test_stats"]
        L.append(f"- اختبار {w['test_period']}: {w['params']} → {ts.get('trades', 0)} صفقة، {ts.get('total_r', 0)}R")
    L += ["", "## حسب نوع الإعداد", ""] + [f"- {k}: {v.get('trades')} صفقة، {v.get('total_r')}R، فوز {v.get('win_rate')}%" for k, v in S["desk_by_setup"].items()]
    L += ["", "## حدود", ""] + [f"- {x}" for x in o["limits"]] + ["", f"> {o['disclosure']}"]
    if S["desk"].get("trades", 0) < 30:
        L.insert(4, "⚠️ أقل من 30 صفقة — النتيجة تُعتبر صدفة ولا يُعتمد عليها.\n")
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
