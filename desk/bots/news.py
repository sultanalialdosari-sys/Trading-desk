# -*- coding: utf-8 -*-
"""📰 News — يقرأ أخبار آخر 24 ساعة من Alpaca News (Benzinga) ويقول: ليش السهم يتحرك؟ وهل فيه خطر؟

تصنيف بالكلمات (بدون ذكاء اصطناعي في القرار) — قد يخطئ، ويعرض العنوان كما هو.
"""
import re
from datetime import datetime
from typing import List, Optional

from desk.alpaca import ET, parse_ts
from desk.bots.base import Opinion

RISK = re.compile(r"\b(offering|dilut\w*|at[- ]the[- ]market|atm program|reverse split|going concern|delist\w*|"
                  r"bankrupt\w*|chapter 11|sec (probe|investigation|charges)|subpoena|fraud|short seller|halted|"
                  r"recall|class action|restat\w*)\b", re.I)
BEAR = re.compile(r"\b(downgrad\w*|cuts? (guidance|outlook|forecast)|misses?|missed|lowers? (guidance|outlook)|"
                  r"price target (cut|lowered)|lowers? price target|layoffs?|weak(er)? (guidance|demand)|plunges?|tumbles?|sinks?)\b", re.I)
BULL = re.compile(r"\b(upgrad\w*|beats?|raises? (guidance|outlook|forecast)|record (revenue|quarter)|"
                  r"price target (raised|boost\w*)|raises? price target|fda approv\w*|approval|awarded|contract|"
                  r"partnership|acquir\w*|buyback|repurchase|soars?|surges?|jumps?|rall(y|ies))\b", re.I)


def classify(text: str) -> str:
    if RISK.search(text):
        return "risk"
    b, s = bool(BULL.search(text)), bool(BEAR.search(text))
    return "bull" if b and not s else "bear" if s and not b else "mixed" if b and s else "plain"


def analyze(sym: str, items: Optional[List[dict]], now: datetime) -> Opinion:
    if items is None:
        return Opinion("news", "unavailable", None, "خدمة الأخبار ما ردّت — ما أقدر أحكم.", {"symbol": sym})
    mine = [n for n in items if sym in (n.get("symbols") or [])]
    if not mine:
        return Opinion("news", "neutral", 50.0, "ما فيه أي خبر خلال 24 ساعة — الحركة بدون محفّز معروف.",
                       {"symbol": sym, "count": 0})
    tagged = []
    for n in mine[:8]:
        text = f"{n.get('headline', '')} {n.get('summary', '')}"
        t = parse_ts(n["created_at"]) if n.get("created_at") else None
        tagged.append({"kind": classify(text), "headline": (n.get("headline") or "")[:160],
                       "source": n.get("source", ""), "hours_ago": round((now - t).total_seconds() / 3600, 1) if t else None,
                       "time_et": t.astimezone(ET).strftime("%m-%d %H:%M") if t else ""})
    kinds = [x["kind"] for x in tagged]
    facts = {"symbol": sym, "count": len(mine), "items": tagged[:4]}
    top = tagged[0]
    when = f"قبل {top['hours_ago']} ساعة" if top["hours_ago"] is not None else ""
    if "risk" in kinds:
        h = next(x for x in tagged if x["kind"] == "risk")
        return Opinion("news", "negative", 10.0, f"⚠️ خبر مخاطرة: «{h['headline']}» — هذا النوع يقتل الصعود.", facts)
    if kinds[0] == "bear" or kinds.count("bear") > kinds.count("bull"):
        return Opinion("news", "negative", 30.0, f"الأخبار سلبية: «{top['headline']}» ({when}).", facts)
    if "bull" in kinds:
        h = next(x for x in tagged if x["kind"] == "bull")
        return Opinion("news", "positive", 75.0 if len(mine) >= 2 else 68.0,
                       f"فيه محفّز: «{h['headline']}» ({h['source']}).", facts)
    return Opinion("news", "neutral", 52.0, f"آخر خبر: «{top['headline']}» ({when}) — ما يبيّن اتجاه واضح.", facts)
