# -*- coding: utf-8 -*-
"""طبقة Gemini — تعيد صياغة كلام البوتات بلهجة طبيعية كأنها محادثة جروب.

قواعد صارمة:
 • القرار لا يتغير أبداً: Gemini يكتب فقط، القواعد هي اللي تقرّر.
 • أي رقم يكتبه Gemini لازم يكون موجود في الحقائق؛ وإلا نرجع للجملة الأصلية (منع الهلوسة).
 • لو ما فيه مفتاح أو فشل الطلب: الجمل الأصلية تُستخدم ولا يتعطل شيء.
"""
import json
import os
import re
from typing import Dict, List, Optional

import requests

API = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent"
ORDER = ["scout", "news", "sentiment", "charts", "skeptic", "chief"]

PROMPT = """أنت تكتب رسائل «جروب تداول» فيه 6 بوتات. لكل فكرة سهم أعطيتك رأي كل بوت (line) والحقائق (facts).
أعد صياغة جملة كل بوت بالعربية السعودية البسيطة، جملة أو جملتين، بشخصيته:
- scout: كشّاف متحمس يلاحظ الحركة
- news: صحفي مختصر، يذكر العنوان كما هو بالإنجليزي بين «»
- sentiment: يقرأ مزاج الناس
- charts: فني دقيق بالأرقام
- skeptic: معارض عنيد، يبدأ بالاعتراض الأقوى، ويوجّه كلامه لـ Charts
- chief: حاسم، يذكر القرار نفسه بالضبط (شراء/انتظار/تخطي) ولا يغيّره
ممنوع: إضافة أي رقم أو سعر أو نسبة أو خبر غير موجود حرفياً في الحقائق أو الجملة. ممنوع تغيير القرار. ممنوع وعود ربح.
اكتب المصطلحات الفنية بالإنجليزي (VWAP, RSI, ATR, RVOL, EMA).
أرجع JSON فقط بالشكل: {"SYMBOL": {"scout": "...", "news": "...", "sentiment": "...", "charts": "...", "skeptic": "...", "chief": "..."}}

الأفكار:
"""

NUM = re.compile(r"\d+(?:[.,]\d+)*")


def _nums(s: str) -> List[str]:
    return [n.replace(",", "") for n in NUM.findall(s)]


def _allowed(src: str) -> set:
    out = set()
    for n in _nums(src):
        out.add(n)
        try:
            f = float(n)
            out.update({f"{f:g}", f"{f:.0f}", f"{f:.1f}", f"{f:.2f}", str(int(f))})
        except ValueError:
            pass
    return out


def safe(new: str, original: str, facts: dict) -> bool:
    if not new or len(new) > 400:
        return False
    allowed = _allowed(original + " " + json.dumps(facts, ensure_ascii=False))
    return all(n in allowed for n in _nums(new))


def narrate(ideas: List[dict], cfg: dict, key: Optional[str] = None, session=None) -> Dict[str, Dict[str, str]]:
    """ideas: [{symbol, opinions:{bot: {line, facts}}}] — يرجع {symbol: {bot: text}} للجمل المقبولة فقط."""
    key = key or os.environ.get("GEMINI_API_KEY")
    if not key or not ideas:
        return {}
    payload = {i["symbol"]: {b: {"line": i["opinions"][b]["line"], "facts": i["opinions"][b]["facts"]} for b in ORDER}
               for i in ideas}
    body = {"contents": [{"parts": [{"text": PROMPT + json.dumps(payload, ensure_ascii=False)}]}],
            "generationConfig": {"temperature": 0.6, "responseMimeType": "application/json"}}
    s = session or requests
    for model in cfg.get("gemini_models", ["gemini-flash-latest", "gemini-2.5-flash"]):
        try:
            r = s.post(API.format(model), params={"key": key}, json=body, timeout=40)
            if r.status_code != 200:
                continue
            txt = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            data = json.loads(txt)
        except Exception:
            continue
        out = {}
        for i in ideas:
            got = data.get(i["symbol"]) or {}
            ok = {}
            for b in ORDER:
                o = i["opinions"][b]
                word = {"BUY": "شراء", "WAIT": "انتظار", "SKIP": "تخط"}.get(o["facts"].get("decision"), "")
                if b == "chief" and word not in got.get(b, ""):
                    continue  # Chief لازم يذكر نفس القرار حرفياً
                if safe(got.get(b, ""), o["line"], o["facts"]):
                    ok[b] = got[b].strip()
            out[i["symbol"]] = ok
        out["_model"] = model
        return out
    return {}
