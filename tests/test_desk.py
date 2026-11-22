# -*- coding: utf-8 -*-
import json
import os
import sys
import tempfile
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from desk.engine import Desk  # noqa: E402
from desk.ledger import Ledger, step_trade  # noqa: E402
from desk.narrator import safe  # noqa: E402
from tests.fake import NOW, FakeAlpaca, FakeTG, fake_senti  # noqa: E402

CFG = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")))


def mk(tmp):
    return Desk(FakeAlpaca(), Ledger(os.path.join(tmp, "d.json")), dict(CFG), FakeTG(),
                narrator=lambda ideas, cfg: {}, senti_fetch=fake_senti, log=lambda *a: None)


def test_cycle_and_buttons():
    with tempfile.TemporaryDirectory() as tmp:
        d = mk(tmp)
        s = d.run(now=NOW)
        print(json.dumps({k: v for k, v in s.items() if k != "board"}, ensure_ascii=False, indent=1))
        for b in s["board"]:
            print(b)
        board = {b["symbol"]: b for b in s["board"]}
        assert "RISK" not in board or board["RISK"]["decision"] == "SKIP"
        if d.tg.sent:
            print("\n---- Telegram ----\n" + d.tg.sent[0])
        # ضغطة شراء على أول فكرة
        ideas = d.L.d["ideas"]
        if ideas:
            iid = ideas[0]["id"]
            d.tg.updates = [{"update_id": 10, "callback_query": {"id": "c1", "data": f"{iid}|BUY",
                                                                "message": {"chat": {"id": 1}, "message_id": 1, "text": "x"}}}]
            d.process_buttons(NOW + timedelta(minutes=15))
            assert d.L.d["tg_offset"] == 11
            assert ideas[0]["user_choice"] == "BUY"
            print("mark:", d.tg.marks)
        d.L.save()
        d2 = mk(tmp)
        d2.L = Ledger(os.path.join(tmp, "d.json"))
        s2 = d2.run(now=NOW, final=True)
        print("daily:", json.dumps(d2.L.d["daily"], ensure_ascii=False))


def test_step_trade():
    import pandas as pd
    idx = pd.date_range("2026-11-24 15:00", periods=4, freq="5min", tz="UTC")
    bars = pd.DataFrame({"open": [10, 10.1, 10.3, 10.6], "high": [10.05, 10.25, 10.5, 11.2], "low": [9.95, 10.0, 10.2, 10.5],
                         "close": [10.0, 10.2, 10.45, 11.1], "volume": [1] * 4}, index=idx)
    tr = {"entry": 10.2, "stop": 9.9, "target": 10.8, "qty": 100, "status": "pending", "created_utc": "2026-11-24T14:59:00+00:00",
          "valid_until_utc": "2026-11-24T16:00:00+00:00", "fill_px": None}
    step_trade(tr, bars, 0.0)
    assert tr["status"] == "won" and tr["r"] == 2.0, tr


def test_gemini_guard():
    assert safe("دخول فوق $10.20 والستوب $9.90", "دخول فوق $10.20، ستوب $9.90", {})
    assert not safe("الهدف $15 مضمون", "دخول فوق $10.20", {"entry": 10.2})





# ---------------------------------------------------------------- Finnhub + SEC
class FakeFH:
    ok = True
    errors = []

    def __init__(self, shift=0.0, age=20):
        self.shift, self.age = shift, age

    def quote(self, sym):
        from tests.fake import FakeAlpaca
        p = float(FakeAlpaca().data[sym]["close"].iloc[-1]) if sym in FakeAlpaca().data else 10.0
        return {"price": p * (1 + self.shift / 100), "time": NOW - timedelta(seconds=self.age), "prev_close": None}

    def news(self, sym, now):
        return [{"symbols": [sym], "headline": f"{sym} wins $20 million contract", "summary": "", "source": "x (Finnhub)",
                 "created_at": (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")}]

    def earnings(self, sym, today):
        return {"date": today.isoformat(), "hour": "amc"} if sym == "TRND" else {}


class FakeSEC:
    ok = True
    errors = []

    def risks(self, sym, now):
        return [{"severity": "veto", "text": "نشرة طرح أسهم (424B5) بتاريخ x"}] if sym == "FLAT" else []


def run_with(fh, sec=None):
    with tempfile.TemporaryDirectory() as tmp:
        cfg = dict(CFG); cfg.update(max_stop_atr=5, wait_score=50, buy_score=54)
        d = Desk(FakeAlpaca(), Ledger(os.path.join(tmp, "d.json")), cfg, FakeTG(), narrator=lambda i, c: {},
                 senti_fetch=fake_senti, log=lambda *a: None, fh=fh, sec=sec or FakeSEC())
        d.run(now=NOW)
        return d


def test_second_source():
    d = run_with(FakeFH(shift=0.1))
    idea = next(i for i in d.L.d["ideas"] if i["symbol"] == "TRND")
    sc = idea["opinions"]["scout"]
    assert sc["facts"]["second_source"] == "Finnhub" and abs(sc["facts"]["price_diff_pct"]) < 0.5, sc["facts"]
    assert "Finnhub" in sc["line"]
    objs = [o["text"] for o in idea["opinions"]["skeptic"]["facts"]["objections"]]
    assert any("بعد الإغلاق" in t for t in objs), objs          # نتائج amc
    print("scout:", sc["line"])
    # فرق سعر كبير = veto
    d2 = run_with(FakeFH(shift=-2.0))
    assert all(b["decision"] == "SKIP" for b in d2.L.d["board"]), d2.L.d["board"]
    print("price diff veto OK")


def test_stale_rescue():
    from desk.bots import skeptic
    from desk.bots.base import Opinion
    so = Opinion("scout", "positive", 70, "", {"symbol": "X", "last_trade_age_sec": 900, "price_diff_pct": 0.1, "finnhub_age_sec": 30})
    ch = Opinion("charts", "positive", 70, "", {"symbol": "X"})
    nw = Opinion("news", "neutral", 50, "", {"count": 1})
    se = Opinion("sentiment", "unavailable", None, "", {})
    r = skeptic.review("X", so, ch, nw, se, {}, NOW, CFG)
    sev = {o["text"][:20]: o["severity"] for o in r.facts["objections"]}
    assert not any("عمره" in t for t in sev), sev
    so.facts["finnhub_age_sec"] = 900
    r = skeptic.review("X", so, ch, nw, se, {}, NOW, CFG)
    assert any("عمره" in o["text"] and o["severity"] == "veto" for o in r.facts["objections"])
    print("stale rescue OK")


def test_sec_and_merge():
    from desk.finnhub import merge_news
    a = [{"headline": "Big News Today", "created_at": "2026-11-24T10:00:00Z"}]
    b = [{"headline": "big news today!", "created_at": "2026-11-24T10:01:00Z"}, {"headline": "Other", "created_at": "2026-11-24T09:00:00Z"}]
    assert len(merge_news(a, b)) == 2 and merge_news(None, None) is None
    d = run_with(FakeFH(), FakeSEC())
    flat = [b for b in d.L.d["board"] if b["symbol"] == "FLAT"]
    assert not flat or flat[0]["decision"] == "SKIP"
    print("sec/merge OK")


if __name__ == "__main__":
    test_step_trade(); test_gemini_guard(); test_cycle_and_buttons()
    test_second_source(); test_stale_rescue(); test_sec_and_merge()
    print("\nALL OK")
