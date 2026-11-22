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


if __name__ == "__main__":
    test_step_trade(); test_gemini_guard(); test_cycle_and_buttons()
    print("\nALL OK")
