# -*- coding: utf-8 -*-
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Opinion:
    bot: str                    # scout / charts / news / sentiment / skeptic / chief
    stance: str                 # positive / negative / neutral / unavailable
    score: Optional[float]      # 0–100 (None = غير متوفر)
    line: str                   # جملة البوت (قالب ثابت — Gemini قد يعيد صياغتها)
    facts: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)


BOTS = {
    "chief": {"name": "Chief", "ar": "القائد", "emoji": "👑", "color": "#111111"},
    "scout": {"name": "Scout", "ar": "الكشّاف", "emoji": "🔭", "color": "#2f6fed"},
    "news": {"name": "News", "ar": "الأخبار", "emoji": "📰", "color": "#f2a900"},
    "sentiment": {"name": "Sentiment", "ar": "المزاج", "emoji": "💬", "color": "#e5338f"},
    "charts": {"name": "Charts", "ar": "الشارت", "emoji": "📈", "color": "#1fb35a"},
    "skeptic": {"name": "Skeptic", "ar": "المشكّك", "emoji": "🛡️", "color": "#7b7f87"},
}


def px(p) -> str:
    if p is None:
        return "غير متوفر"
    return f"${p:,.2f}" if p >= 1 else f"${p:.4f}"


def pct(x, signed=True) -> str:
    return f"{x:+.1f}%" if signed else f"{x:.1f}%"
