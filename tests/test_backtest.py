# -*- coding: utf-8 -*-
"""اختبار أداة الاختبار التاريخي على سوق وهمي (بدون إنترنت) + فحص عدم النظر للمستقبل."""
import json
import os
import sys
import time
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from desk import backtest as BT  # noqa: E402
from desk.alpaca import ET, UTC  # noqa: E402

CFG = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")))


def market(n_days=45, seed=0, base=50.0):
    rng = np.random.default_rng(seed)
    rows, price = [], base
    d = date(2026, 8, 3)
    k = 0
    while k < n_days:
        if d.weekday() < 5:
            drift = rng.choice([0.0015, 0.0, -0.001], p=[0.3, 0.5, 0.2])
            vm = 2.5 if drift > 0 else 1.0
            t0 = datetime(d.year, d.month, d.day, 9, 30, tzinfo=ET)
            for i in range(78):
                o = price
                c = o * (1 + drift * (1 if i < 40 else -0.3) + rng.normal(0, 0.0015))
                rows.append({"time": (t0 + timedelta(minutes=5 * i)).astimezone(UTC), "open": o,
                             "high": max(o, c) * (1 + abs(rng.normal(0, 0.0008))),
                             "low": min(o, c) * (1 - abs(rng.normal(0, 0.0008))), "close": c,
                             "volume": float(rng.integers(20000, 40000)) * vm})
                price = c
            k += 1
        d += timedelta(days=1)
    return pd.DataFrame(rows).set_index("time")


def test_backtest():
    syms = ["AAA", "BBB", "CCC", "DDD"]
    bars = {s: market(seed=i + 1, base=30 + 10 * i) for i, s in enumerate(syms)}
    bars["SPY"], bars["QQQ"] = market(seed=11, base=600), market(seed=12, base=520)
    days = sorted(set(bars["SPY"].index.tz_convert(ET).date))[14:]
    news_items = [{"symbols": ["AAA"], "headline": "AAA announces $40 million public offering",
                   "created_at": datetime.combine(days[3], datetime.min.time(), tzinfo=ET).replace(hour=10).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}]
    t = time.time()
    snaps = BT.collect(bars, news_items, syms, days, CFG, log=lambda *a: None)
    t_collect = time.time() - t
    trades = BT.decide(snaps, CFG)
    s = BT.summarize(trades)
    wf = BT.walk_forward(snaps, days, CFG, train=15, test=5, min_trades=1)
    print(f"snaps={len(snaps)} trades={len(trades)} collect={t_collect:.1f}s")
    print(json.dumps({k: s[k] for k in ("desk", "wait", "blocked")}, ensure_ascii=False, indent=1))
    print(json.dumps(wf["out_of_sample"], ensure_ascii=False), wf["params_stable"], len(wf["windows"]))

    # 1) لا نظر للمستقبل: كل لحظة تشوف شموع انتهت قبلها فقط
    for sn in snaps[:300]:
        bt = sn.ch.facts.get("bar_time_et")      # آخر شمعة استخدمها Charts
        last_bar = datetime.combine(sn.now.astimezone(ET).date(), datetime.strptime(bt, "%H:%M").time(), tzinfo=ET)
        assert last_bar + timedelta(minutes=5) <= sn.now, (bt, sn.now)
    # 2) يوم خبر الطرح: لا صفقة «مكتب» على AAA في الـ24 ساعة بعده
    nt = datetime.fromisoformat(news_items[0]["created_at"].replace("Z", "+00:00"))
    bad = [x for x in trades if x["symbol"] == "AAA" and x["owner"] == "desk"
           and nt <= datetime.fromisoformat(x["created_utc"]) <= nt + timedelta(hours=24)]
    assert not bad, bad
    # 3) الصفقات تُفتح بعد لحظة الفكرة فقط
    for x in trades:
        if x.get("fill_utc"):
            assert x["fill_utc"] > x["created_utc"]
    import backtest as CLI
    v1, v2 = CLI.load_profiles()
    tr2 = BT.decide(snaps, v2)
    abl = {k: {"label": CLI.NAMES[k], **BT.r_stats([t["r"] for t in BT.done(BT.decide(snaps, {**v1, **o})) if t["owner"] == "desk"]), "delta_total_r": 0}
           for k, o in CLI.ABLATIONS.items()}
    md = CLI.report_md({"period": [str(days[0]), str(days[-1])], "days": len(days), "symbols": syms, "feed": "fake", "snaps": len(snaps),
                        "versions": {"v1": {"label": "v1", "summary": s, "walk_forward": wf},
                                     "v2": {"label": "v2", "summary": BT.summarize(tr2), "walk_forward": wf}},
                        "ablation": abl, "limits": ["x"], "disclosure": CLI.DISCLOSURE})
    print(md[:2500])


if __name__ == "__main__":
    test_backtest()
    print("\nBACKTEST OK")
