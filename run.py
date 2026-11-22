# -*- coding: utf-8 -*-
"""تشغيل دورة واحدة لمكتب التداول.

  python run.py          ← دورة أثناء الجلسة (يتجاهل إذا السوق مقفل، لكن يقرأ أزرارك)
  python run.py --final  ← بعد الإغلاق: إقفال الصفقات الافتراضية + ملخص اليوم
"""
import json
import os
import sys

from desk.alpaca import Alpaca
from desk.bots.base import BOTS
from desk.engine import Desk
from desk.finnhub import Finnhub
from desk.sec import Sec
from desk.ledger import Ledger, stats
from desk.telegram import Telegram

ROOT = os.path.dirname(os.path.abspath(__file__))


def export_site(L: Ledger, path: str):
    d = L.d
    out = {"last_run": d.get("last_run"), "last_open": d.get("last_open"), "market": d.get("market"),
           "board": d.get("board", []), "bots": BOTS,
           "ideas": list(reversed(d["ideas"]))[:40],
           "trades": list(reversed(d["trades"]))[:120],
           "stats": {o: stats(d["trades"], o) for o in ("desk", "you", "blocked")},
           "daily": d.get("daily", {}), "runs": d["runs"][-12:]}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False)


def main():
    final = "--final" in sys.argv
    cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
    key, sec = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_API_SECRET")
    if not key or not sec:
        sys.exit("ضيف ALPACA_API_KEY و ALPACA_API_SECRET في GitHub Secrets.")
    L = Ledger(os.path.join(ROOT, "data", "desk.json"))
    fh, sc = Finnhub(), Sec()
    desk = Desk(Alpaca(key, sec), L, cfg, Telegram(), fh=fh, sec=sc)
    s = desk.run(final=final)
    L.save()
    export_site(L, os.path.join(ROOT, "docs", "desk.json"))
    print(json.dumps({k: v for k, v in s.items() if k != "board"}, ensure_ascii=False, indent=1, default=str))
    if not Telegram().ok:
        print("ملاحظة: TELEGRAM_TOKEN/TELEGRAM_CHAT_ID غير مضبوطة — النتائج على الموقع فقط.")
    if not fh.ok:
        print("ملاحظة: FINNHUB_API_KEY غير مضبوط — ما فيه تحقق ثاني من السعر ولا أخبار Finnhub.")
    if not sc.ok:
        print("ملاحظة: SEC_USER_AGENT غير مضبوط (اسم + بريد) — فحص إفصاحات الطرح متوقف.")
    for e in (fh.errors + sc.errors)[:5]:
        print("تنبيه:", e)
    if not os.environ.get("GEMINI_API_KEY"):
        print("ملاحظة: GEMINI_API_KEY غير مضبوط — البوتات تتكلم بالقوالب الثابتة.")


if __name__ == "__main__":
    main()
