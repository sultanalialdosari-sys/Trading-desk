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
    out = {"setup": d.get("setup"), "last_run": d.get("last_run"), "last_open": d.get("last_open"), "market": d.get("market"),
           "board": d.get("board", []), "bots": BOTS,
           "ideas": list(reversed(d["ideas"]))[:40],
           "trades": list(reversed(d["trades"]))[:120],
           "stats": {o: stats(d["trades"], o) for o in ("desk", "you", "blocked")},
           "daily": d.get("daily", {}), "runs": d["runs"][-12:]}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False)


def load_cfg(profile: str = None) -> dict:
    """الإعدادات الأساسية (النسخة 1) + فوقها النسخة المختارة من profiles."""
    cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
    name = profile or cfg.get("profile", "v1")
    cfg.update({k: v for k, v in cfg.get("profiles", {}).get(name, {}).items() if not k.startswith("_")})
    cfg["active_profile"] = name
    return cfg


def self_check(desk, fh, sc) -> dict:
    """فحص سريع لكل مصدر — يُحفظ في desk.json عشان نعرف وش شغال بدون ما نفتح السجلات."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    out = {"utc": now.isoformat(), "alpaca": "ok" if not any("clock" in e for e in desk.errors) else "fail"}
    tg = desk.tg
    if tg and tg.ok:
        try:
            me = tg.call("getMe")
            out["telegram"] = "ok @" + me.get("username", "")
            if not desk.L.d.get("tg_hello"):
                tg.send("✅ مكتب التداول متصل. أول فكرة توصلك هنا لما يفتح السوق (5:30 العصر بتوقيت الرياض).")
                desk.L.d["tg_hello"] = now.isoformat()
        except Exception as e:
            out["telegram"] = "fail: " + str(e)[:80]
    else:
        out["telegram"] = "missing"
    if fh.ok:
        q = fh.quote("SPY")
        out["finnhub"] = f"ok SPY {q['price']}" if q else "fail: " + "; ".join(fh.errors[-2:])
    else:
        out["finnhub"] = "missing"
    if sc.ok:
        out["sec"] = "ok" if sc.cik("AAPL") else "fail: " + "; ".join(sc.errors[-2:])
    else:
        out["sec"] = "missing"
    out["gemini"] = "set" if os.environ.get("GEMINI_API_KEY") else "missing"
    return out


def main():
    final = "--final" in sys.argv
    cfg = load_cfg()
    key, sec = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_API_SECRET")
    if not key or not sec:
        sys.exit("ضيف ALPACA_API_KEY و ALPACA_API_SECRET في GitHub Secrets.")
    L = Ledger(os.path.join(ROOT, "data", "desk.json"))
    fh, sc = Finnhub(), Sec()
    desk = Desk(Alpaca(key, sec), L, cfg, Telegram(), fh=fh, sec=sc)
    s = desk.run(final=final)
    L.d["setup"] = self_check(desk, fh, sc)
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
