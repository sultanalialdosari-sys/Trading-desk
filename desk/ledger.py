# -*- coding: utf-8 -*-
"""سجل المكتب (data/desk.json): الأفكار، قراراتك، والصفقات الافتراضية ونتائجها.

ثلاث محافظ افتراضية تُقاس منفصلة:
 • desk    — كل فكرة قرارها «شراء» (أداء المكتب نفسه)
 • you     — الأفكار اللي ضغطت عليها «شراء» (أداؤك أنت)
 • blocked — أفكار أوقفها Skeptic وكان لها تذكرة (هل أنقذنا فعلاً؟)
المحاكاة متحفّظة: لو شمعة وحدة لمست الستوب والهدف معاً نحسبها خسارة.
"""
import json
import os
from datetime import datetime, timedelta
from typing import List, Optional

import pandas as pd

from desk.alpaca import UTC

EMPTY = {"tg_offset": 0, "ideas": [], "trades": [], "runs": [], "daily": {}}


class Ledger:
    def __init__(self, path: str):
        self.path = path
        self.d = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else json.loads(json.dumps(EMPTY))
        for k, v in EMPTY.items():
            self.d.setdefault(k, v if not isinstance(v, (list, dict)) else type(v)())

    def save(self, keep_days: int = 45):
        cut = (datetime.now(UTC) - timedelta(days=keep_days)).isoformat()
        self.d["ideas"] = [i for i in self.d["ideas"] if i["created_utc"] >= cut]
        self.d["trades"] = [t for t in self.d["trades"] if t["created_utc"] >= cut]
        self.d["runs"] = self.d["runs"][-60:]
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        json.dump(self.d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    # ---------------- الأفكار ----------------
    def recent_idea(self, sym: str, now: datetime, minutes: int) -> Optional[dict]:
        cut = (now - timedelta(minutes=minutes)).isoformat()
        xs = [i for i in self.d["ideas"] if i["symbol"] == sym and i["created_utc"] >= cut]
        return xs[-1] if xs else None

    def add_idea(self, idea: dict):
        self.d["ideas"].append(idea)

    def idea(self, idea_id: str) -> Optional[dict]:
        return next((i for i in self.d["ideas"] if i["id"] == idea_id), None)

    # ---------------- الصفقات ----------------
    def open_trade(self, idea: dict, owner: str, now: datetime, valid_min: int):
        t = idea["ticket"]
        if any(x["idea_id"] == idea["id"] and x["owner"] == owner for x in self.d["trades"]):
            return None
        tr = {"id": f"{idea['id']}-{owner}", "idea_id": idea["id"], "symbol": idea["symbol"], "owner": owner,
              "status": "pending", "entry": t["entry"], "stop": t["stop"], "target": t["target"], "qty": t["qty"],
              "created_utc": now.isoformat(), "valid_until_utc": (now + timedelta(minutes=valid_min)).isoformat(),
              "fill_px": None, "fill_utc": None, "exit_px": None, "exit_utc": None, "r": None, "pnl": None}
        self.d["trades"].append(tr)
        return tr

    def active(self) -> List[dict]:
        return [t for t in self.d["trades"] if t["status"] in ("pending", "open")]


def step_trade(tr: dict, bars: pd.DataFrame, slip_pct: float) -> None:
    """يحدّث صفقة على شموع 5m (UTC index) بعد آخر وقت عولجت عنده."""
    if bars is None or bars.empty:
        return
    since = pd.Timestamp(tr.get("checked_utc") or tr["created_utc"])
    xs = bars[bars.index > since]
    for ts, b in xs.iterrows():
        tsi = ts.isoformat()
        if tr["status"] == "pending":
            if tsi > tr["valid_until_utc"]:
                tr["status"] = "expired"
                break
            if b["high"] >= tr["entry"]:
                fill = max(tr["entry"], b["open"]) * (1 + slip_pct / 100)
                tr.update(status="open", fill_px=round(fill, 4), fill_utc=tsi)
                if b["low"] <= tr["stop"]:
                    _close(tr, tr["stop"], tsi, slip_pct, "lost")
                    break
                continue
        if tr["status"] == "open":
            if b["low"] <= tr["stop"]:
                _close(tr, min(tr["stop"], b["open"]), tsi, slip_pct, "lost")
                break
            if b["high"] >= tr["target"]:
                _close(tr, tr["target"], tsi, slip_pct, "won")
                break
        tr["checked_utc"] = tsi
    if len(xs):
        tr["checked_utc"] = xs.index[-1].isoformat()


def _close(tr, px, tsi, slip_pct, status):
    exit_px = px * (1 - slip_pct / 100)
    risk = tr["entry"] - tr["stop"]
    tr.update(status=status, exit_px=round(exit_px, 4), exit_utc=tsi,
              r=round((exit_px - tr["fill_px"]) / risk, 2) if risk > 0 else None,
              pnl=round((exit_px - tr["fill_px"]) * tr["qty"], 2))


def close_eod(tr: dict, last_px: Optional[float], now: datetime, slip_pct: float):
    if tr["status"] == "pending":
        tr["status"] = "expired"
    elif tr["status"] == "open" and last_px:
        _close(tr, last_px, now.isoformat(), slip_pct, "closed")


def stats(trades: List[dict], owner: str) -> dict:
    xs = [t for t in trades if t["owner"] == owner and t["status"] in ("won", "lost", "closed")]
    n = len(xs)
    wins = [t for t in xs if (t["r"] or 0) > 0]
    return {"owner": owner, "closed": n, "wins": len(wins), "win_rate": round(len(wins) / n * 100) if n else None,
            "total_r": round(sum(t["r"] or 0 for t in xs), 2), "avg_r": round(sum(t["r"] or 0 for t in xs) / n, 2) if n else None,
            "pnl": round(sum(t["pnl"] or 0 for t in xs), 2),
            "open": len([t for t in trades if t["owner"] == owner and t["status"] in ("pending", "open")]),
            "expired": len([t for t in trades if t["owner"] == owner and t["status"] == "expired"])}
