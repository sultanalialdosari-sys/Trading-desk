# -*- coding: utf-8 -*-
"""تيليجرام: رسالة «مكتب التداول» مع أزرار شراء/انتظار/تخطي، وقراءة الضغطات.

النظام يشتغل على GitHub Actions كل 15 دقيقة (ما فيه سيرفر شغّال دائماً)،
فضغطة الزر تُقرأ في الدورة التالية (getUpdates) — يعني تأخير حتى 15 دقيقة.
"""
import os
from typing import List, Optional

import requests

from desk.bots.base import BOTS, px

API = "https://api.telegram.org/bot{}/{}"
ORDER = ["scout", "news", "sentiment", "charts", "skeptic", "chief"]
DEC_AR = {"BUY": "🟢 شراء", "WAIT": "🟡 انتظار", "SKIP": "🔴 تخطّي"}


class Telegram:
    def __init__(self, token: Optional[str] = None, chat_id: Optional[str] = None, session=None):
        self.token = token or os.environ.get("TELEGRAM_TOKEN")
        self.chat = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
        self.s = session or requests

    @property
    def ok(self) -> bool:
        return bool(self.token and self.chat)

    def call(self, method: str, **params):
        r = self.s.post(API.format(self.token, method), json=params, timeout=20)
        j = r.json()
        if not j.get("ok"):
            raise RuntimeError(f"Telegram {method}: {j.get('description')}")
        return j["result"]

    def send_idea(self, idea: dict, site_url: str = "") -> Optional[int]:
        kb = {"inline_keyboard": [[
            {"text": "✅ شراء", "callback_data": f"{idea['id']}|BUY"},
            {"text": "⏳ انتظار", "callback_data": f"{idea['id']}|WAIT"},
            {"text": "❌ تخطّي", "callback_data": f"{idea['id']}|SKIP"}]]}
        res = self.call("sendMessage", chat_id=self.chat, text=format_idea(idea, site_url), reply_markup=kb,
                        disable_web_page_preview=True)
        return res["message_id"]

    def send(self, text: str):
        return self.call("sendMessage", chat_id=self.chat, text=text, disable_web_page_preview=True)

    def poll(self, offset: int) -> List[dict]:
        return self.call("getUpdates", offset=offset, timeout=0, allowed_updates=["callback_query"])

    def ack(self, cq_id: str, text: str):
        try:
            self.call("answerCallbackQuery", callback_query_id=cq_id, text=text)
        except Exception:
            pass  # الضغطة قديمة (>15 دقيقة) — تيليجرام يرفض الرد عليها، عادي

    def mark(self, chat_id, message_id, note: str, original: str):
        try:
            self.call("editMessageText", chat_id=chat_id, message_id=message_id, text=original + "\n\n" + note,
                      disable_web_page_preview=True)
        except Exception:
            pass


def format_idea(idea: dict, site_url: str = "") -> str:
    ch = idea["opinions"]["chief"]["facts"]
    t = ch["ticket"]
    L = [f"🏛️ مكتب التداول — {idea['symbol']}  •  {idea['time_et']} نيويورك", ""]
    for b in ORDER:
        meta = BOTS[b]
        L.append(f"{meta['emoji']} {meta['name']}: {idea['text'][b]}")
    L += ["", f"🧾 تذكرة الأمر — {DEC_AR[ch['decision']]}  ({idea['score']}/100)"]
    if t:
        L += [f"دخول فوق {px(t['entry'])} • ستوب {px(t['stop'])} • هدف {px(t['target'])}",
              f"الكمية {t['qty']} سهم ≈ ${t['position_usd']:,.0f} • مخاطرة ${t['risk_usd']} • عائد ${t['reward_usd']} (R:R 1:{t['rr']})"]
    L += ["", f"⏱️ {idea['data_note']}", "تجربة على Paper — ليست توصية."]
    if site_url:
        L.append(site_url)
    return "\n".join(L)
