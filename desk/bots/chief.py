# -*- coding: utf-8 -*-
"""👑 Chief — يجمع آراء الفريق ويطلع بقرار واحد: BUY / WAIT / SKIP، مع تذكرة الأمر.

الأوزان (من 100): Charts 45 • Scout 20 • News 15 • Sentiment 10 • السوق العام 10
ثم يخصم اعتراضات Skeptic. أي veto = SKIP مهما كانت الدرجة.
البوت اللي «غير متوفر» يُستبعد ويُعاد توزيع وزنه (ولا يُحسب كأنه محايد).
"""
from math import floor

from desk.bots.base import Opinion, px

W = {"charts": 45, "scout": 20, "news": 15, "sentiment": 10, "market": 10}


def decide(sym: str, ops: dict, market: dict, cfg: dict) -> Opinion:
    parts, wsum = {}, 0.0
    for k in ("charts", "scout", "news", "sentiment"):
        o = ops[k]
        if o.score is not None:
            parts[k] = o.score
            wsum += W[k]
    if market.get("score") is not None:
        parts["market"] = market["score"]
        wsum += W["market"]
    base = sum(parts[k] * W[k] for k in parts) / wsum if wsum else 0
    sk = ops["skeptic"].facts
    score = round(max(0, base - sk["penalty"]), 1)
    c = ops["charts"].facts
    ticket = None
    if "entry" in c and c["entry"] > c["stop"]:
        risk = c["entry"] - c["stop"]
        qty = floor(min(cfg["risk_usd"] / risk, cfg["max_position_usd"] / c["entry"]))
        ticket = {"entry": c["entry"], "stop": c["stop"], "target": c["target"], "qty": max(qty, 0),
                  "risk_usd": round(qty * risk, 2), "reward_usd": round(qty * (c["target"] - c["entry"]), 2),
                  "rr": c.get("rr"), "position_usd": round(qty * c["entry"], 2)}

    if sk["veto"] or not ticket or ticket["qty"] < 1:
        decision = "SKIP"
    elif score >= cfg["buy_score"]:
        decision = "BUY"
    elif score >= cfg["wait_score"]:
        decision = "WAIT"
    else:
        decision = "SKIP"

    unavailable = [k for k in ("charts", "news", "sentiment") if ops[k].score is None]
    agree = [k for k in ("scout", "charts", "news", "sentiment") if ops[k].stance == "positive"]
    names = {"scout": "Scout", "charts": "Charts", "news": "News", "sentiment": "Sentiment"}
    agree_txt = "، ".join(names[k] for k in agree) or "ولا واحد"
    if decision == "BUY":
        line = (f"القرار: شراء — بس فوق {px(ticket['entry'])} فقط. "
                f"الدرجة {score}/100، موافقين: {agree_txt}. {ticket['qty']} سهم، مخاطرة ${ticket['risk_usd']}.")
    elif decision == "WAIT":
        line = (f"القرار: انتظار. الدرجة {score}/100 — فيه فكرة بس ناقصها تأكيد. "
                f"لو اخترق {px(ticket['entry'])} بحجم قوي نعيد النظر.")
    else:
        why = "Skeptic أوقفها" if sk["veto"] else "ما فيه خطة دخول" if not ticket else f"الدرجة {score}/100 ما تكفي"
        line = f"القرار: تخطّي — {why}."
    if unavailable:
        line += " (غير متوفر: " + "، ".join(unavailable) + ")"
    return Opinion("chief", "positive" if decision == "BUY" else "neutral" if decision == "WAIT" else "negative", score, line,
                   {"symbol": sym, "decision": decision, "base_score": round(base, 1), "penalty": sk["penalty"],
                    "parts": {k: round(v, 1) for k, v in parts.items()}, "ticket": ticket, "blocked": sk["veto"]})
