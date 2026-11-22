# -*- coding: utf-8 -*-
"""دورة واحدة لمكتب التداول: أزرارك ← السوق ← Scout ← Charts ← News/Sentiment ← Skeptic ← Chief ← تيليجرام ← متابعة الصفقات."""
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional

import pandas as pd

from desk import ta
from desk.alpaca import ET, Alpaca, UTC
from desk.bots import charts, chief, news, scout, sentiment, skeptic
from desk.bots.sentiment import Reddit
from desk.bots.base import Opinion, px
from desk.finnhub import Finnhub, merge_news
from desk.sec import Sec
from desk.ledger import Ledger, close_eod, stats, step_trade
from desk.narrator import ORDER, narrate
from desk.telegram import DEC_AR, Telegram, format_idea

DATA_NOTE = "أسعار IEX لحظية (بورصة واحدة) — الأحجام جزئية لكن النِّسب متّسقة"


class Desk:
    def __init__(self, client: Alpaca, ledger: Ledger, cfg: dict, tg: Optional[Telegram] = None,
                 narrator: Callable = narrate, senti_fetch: Callable = sentiment.fetch, log=print,
                 fh: Optional[Finnhub] = None, sec: Optional[Sec] = None):
        self.c, self.L, self.cfg, self.tg, self.narr, self.sf, self.log = client, ledger, cfg, tg, narrator, senti_fetch, log
        self.fh, self.sec = fh, sec
        self.reddit = Reddit() if senti_fetch is sentiment.fetch else None
        self.errors: List[str] = []

    # ------------------------------------------------------------ أزرار تيليجرام
    def process_buttons(self, now: datetime):
        if not (self.tg and self.tg.ok):
            return
        try:
            ups = self.tg.poll(self.L.d["tg_offset"])
        except Exception as e:
            self.errors.append(f"تيليجرام: {e}")
            return
        for u in ups:
            self.L.d["tg_offset"] = u["update_id"] + 1
            cq = u.get("callback_query")
            if not cq or "|" not in (cq.get("data") or ""):
                continue
            idea_id, choice = cq["data"].split("|", 1)
            idea = self.L.idea(idea_id)
            if not idea:
                self.tg.ack(cq["id"], "الفكرة قديمة وانحذفت من السجل")
                continue
            if idea.get("user_choice"):
                self.tg.ack(cq["id"], f"سجّلت قرارك قبل: {idea['user_choice']}")
                continue
            idea.update(user_choice=choice, user_choice_utc=now.isoformat())
            note = f"👤 قرارك: {DEC_AR[choice]} — سُجّل {now.astimezone(ET).strftime('%H:%M')} نيويورك"
            if choice == "BUY" and idea.get("ticket"):
                same_day = idea["created_utc"][:10] == now.isoformat()[:10]
                if same_day:
                    self.L.open_trade(idea, "you", now, self.cfg["valid_min"])
                    note += " • صفقتك الافتراضية تنتظر اختراق " + f"{idea['ticket']['entry']}"
                    if self.cfg.get("place_paper_orders"):
                        try:
                            t = idea["ticket"]
                            self.c.paper_bracket(idea["symbol"], t["qty"], t["entry"], t["stop"], t["target"])
                            note += " • أُرسل أمر Paper إلى Alpaca"
                        except Exception as e:
                            note += f" • فشل أمر Paper: {str(e)[:80]}"
                else:
                    note += " • الفكرة من يوم سابق، ما فتحت صفقة"
            self.tg.ack(cq["id"], "تم ✅")
            msg = cq.get("message") or {}
            if msg:
                self.tg.mark(msg["chat"]["id"], msg["message_id"], note, msg.get("text", ""))

    # ------------------------------------------------------------ السوق العام
    def market(self, bars: Dict[str, pd.DataFrame], now: datetime) -> dict:
        out = {"score": None}
        res = {}
        for s in ("SPY", "QQQ"):
            d = ta.regular_session(charts.drop_forming(bars.get(s, pd.DataFrame()), now))
            if d.empty:
                continue
            today = d[d.index.date == d.index[-1].date()]
            if today.empty or d.index[-1].date() != now.astimezone(ET).date():
                continue
            vw = float(ta.vwap(today).iloc[-1])
            close = float(today["close"].iloc[-1])
            res[s] = {"close": round(close, 2), "vwap": round(vw, 2), "chg_open_pct": round((close / today["open"].iloc[0] - 1) * 100, 2)}
            out[f"{s.lower()}_below_vwap"] = close < vw
        if res:
            above = sum(1 for s in res if not out[f"{s.lower()}_below_vwap"])
            out["score"] = {0: 25, 1: 50, 2: 75}[above] if len(res) == 2 else (70 if above else 30)
        out["detail"] = res
        return out

    # ------------------------------------------------------------ الدورة
    def run(self, now: Optional[datetime] = None, final: bool = False) -> dict:
        now = now or datetime.now(UTC)
        cfg = self.cfg
        self.process_buttons(now)
        try:
            clk = self.c.clock()
        except Exception as e:
            self.errors.append(f"clock: {e}")
            clk = {"is_open": False}
        is_open = bool(clk.get("is_open"))
        summary = {"utc": now.isoformat(), "market_open": is_open, "final": final, "scanned": 0, "candidates": 0,
                   "ideas": [], "sent": 0, "errors": self.errors, "board": []}

        if is_open and not final:
            self._scan(now, summary)
        self._track(now, final)
        if final:
            self._daily(now)
        summary["api_calls"] = self.c.calls
        self.L.d["runs"].append({k: v for k, v in summary.items() if k != "board"})
        self.L.d["board"] = summary["board"]
        self.L.d["market"] = summary.get("market")
        self.L.d["last_run"] = summary["utc"]
        self.L.d["last_open"] = is_open
        return summary

    def _scan(self, now: datetime, summary: dict):
        cfg = self.cfg
        syms, gainers = scout.universe(self.c, cfg)
        self.errors += [g[1:] for g in gainers if g.startswith("!")]
        gainers = [g for g in gainers if not g.startswith("!")]
        snaps = self.c.snapshots(syms)
        scouted = scout.scan(snaps, gainers, cfg, now)
        summary["scanned"] = len(syms)
        top = scouted[:cfg["scout_top"]]
        if not top:
            return
        start = now - timedelta(days=cfg["history_days"])
        bars = self.c.bars([o.facts["symbol"] for o in top] + ["SPY", "QQQ"], "5Min", start)
        mkt = self.market(bars, now)
        summary["market"] = mkt

        evaluated = []
        for so in top:
            sym = so.facts["symbol"]
            ch = charts.analyze(sym, bars.get(sym, pd.DataFrame()), now, cfg)
            evaluated.append((so, ch))
        deep = [(s, c) for s, c in evaluated if "entry" in c.facts or (c.score or 0) >= 60][:cfg["deep_max"]]
        summary["candidates"] = len(deep)
        try:
            items = self.c.news([s.facts["symbol"] for s, _ in deep]) if deep else []
        except Exception as e:
            items = None
            self.errors.append(f"news: {str(e)[:80]}")

        built = []
        for so, ch in deep:
            sym = so.facts["symbol"]
            self.cross_check(so, now)
            sym_items = items
            if self.fh and self.fh.ok:
                sym_items = merge_news([n for n in (items or []) if sym in (n.get("symbols") or [])] if items is not None else None,
                                       self.fh.news(sym, now))
            nw = news.analyze(sym, sym_items, now)
            se = sentiment.analyze(sym, self.sf(sym), now, self.reddit.get(sym) if self.reddit else None)
            extra = {"sec": self.sec.risks(sym, now) if self.sec else None,
                     "earnings": self.fh.earnings(sym, now.astimezone(ET).date()) if self.fh and self.fh.ok else None}
            sk = skeptic.review(sym, so, ch, nw, se, mkt, now, cfg, extra)
            ops = {"scout": so, "charts": ch, "news": nw, "sentiment": se, "skeptic": sk}
            cf = chief.decide(sym, ops, mkt, cfg)
            ops["chief"] = cf
            built.append((sym, ops, cf))
            summary["board"].append({"symbol": sym, "decision": cf.facts["decision"], "score": cf.score,
                                     "blocked": cf.facts["blocked"], "price": so.facts["price"],
                                     "change_pct": so.facts["change_pct"], "skeptic": sk.line[:140]})

        # أفكار تستحق التسجيل
        keep = []
        for sym, ops, cf in sorted(built, key=lambda x: x[2].score, reverse=True):
            dec = cf.facts["decision"]
            shadow = cf.facts["blocked"] and cf.facts["ticket"] and cf.facts["base_score"] >= cfg["wait_score"]
            if dec == "SKIP" and not shadow:
                continue
            prev = self.L.recent_idea(sym, now, cfg["cooldown_min"])
            if prev and not (prev["decision"] == "WAIT" and dec == "BUY"):
                continue
            keep.append((sym, ops, cf))

        idea_objs = []
        for sym, ops, cf in keep:
            idea_objs.append({
                "id": f"{now.astimezone(ET).strftime('%m%d%H%M')}{sym}", "symbol": sym, "created_utc": now.isoformat(),
                "time_et": now.astimezone(ET).strftime("%H:%M"), "decision": cf.facts["decision"], "score": cf.score,
                "blocked": cf.facts["blocked"], "ticket": cf.facts["ticket"], "data_note": DATA_NOTE,
                "opinions": {b: ops[b].to_dict() for b in ORDER}, "text": {b: ops[b].line for b in ORDER},
                "user_choice": None, "msg_id": None, "narrated_by": None})

        to_send = [i for i in idea_objs if i["decision"] in ("BUY", "WAIT")][:cfg["max_alerts_per_run"]]
        try:
            nar = self.narr(to_send, cfg) if to_send else {}
        except Exception as e:
            nar = {}
            self.errors.append(f"gemini: {str(e)[:80]}")
        for i in to_send:
            got = nar.get(i["symbol"]) or {}
            i["text"].update(got)
            if got:
                i["narrated_by"] = nar.get("_model")

        for i in idea_objs:
            self.L.add_idea(i)
            if i["decision"] == "BUY":
                self.L.open_trade(i, "desk", now, cfg["valid_min"])
            if i["blocked"]:
                self.L.open_trade(i, "blocked", now, cfg["valid_min"])
            summary["ideas"].append(f"{i['symbol']}:{i['decision']}{'/blocked' if i['blocked'] else ''}")
        for i in to_send:
            if self.tg and self.tg.ok:
                try:
                    i["msg_id"] = self.tg.send_idea(i, cfg.get("site_url", ""))
                    summary["sent"] += 1
                except Exception as e:
                    self.errors.append(f"تيليجرام إرسال: {str(e)[:80]}")

    def cross_check(self, so: Opinion, now: datetime):
        """يقارن سعر IEX بسعر Finnhub اللحظي، ويكتب النتيجة في حقائق Scout وكلامه."""
        if not (self.fh and self.fh.ok):
            so.facts["second_source"] = "غير مفعّل"
            return
        q = self.fh.quote(so.facts["symbol"])
        if not q:
            so.facts["second_source"] = "Finnhub ما ردّ"
            so.line += " (Finnhub ما ردّ — السعر من IEX فقط)"
            return
        diff = (so.facts["price"] / q["price"] - 1) * 100
        age = max(0, round((now - q["time"]).total_seconds()))
        so.facts.update(finnhub_price=round(q["price"], 4), finnhub_age_sec=age, price_diff_pct=round(diff, 2),
                        second_source="Finnhub")
        ok = "✅" if abs(diff) <= self.cfg.get("price_diff_major_pct", 0.5) else "⚠️"
        so.line += f" {ok} Finnhub: {px(q['price'])} (فرق {diff:+.2f}%، قبل {age} ث)."

    def _track(self, now: datetime, final: bool):
        act = self.L.active()
        if not act:
            return
        syms = sorted({t["symbol"] for t in act})
        start = min(datetime.fromisoformat(t["created_utc"]) for t in act) - timedelta(minutes=10)
        try:
            bars = self.c.bars(syms, "5Min", start)
        except Exception as e:
            self.errors.append(f"متابعة: {str(e)[:80]}")
            return
        for t in act:
            b = charts.drop_forming(bars.get(t["symbol"], pd.DataFrame()), now)
            step_trade(t, b, self.cfg["slippage_pct"])
            if final and t["status"] in ("pending", "open"):
                last = float(b["close"].iloc[-1]) if len(b) else None
                close_eod(t, last, now, self.cfg["slippage_pct"])

    def _daily(self, now: datetime):
        day = now.astimezone(ET).strftime("%Y-%m-%d")
        tr = [t for t in self.L.d["trades"] if datetime.fromisoformat(t["created_utc"]).astimezone(ET).strftime("%Y-%m-%d") == day]
        ideas = [i for i in self.L.d["ideas"] if datetime.fromisoformat(i["created_utc"]).astimezone(ET).strftime("%Y-%m-%d") == day]
        s = {o: stats(tr, o) for o in ("desk", "you", "blocked")}
        self.L.d["daily"][day] = {"ideas": len(ideas), "buy": sum(i["decision"] == "BUY" for i in ideas),
                                  "wait": sum(i["decision"] == "WAIT" for i in ideas),
                                  "blocked": sum(i["blocked"] for i in ideas), "stats": s}
        if self.tg and self.tg.ok:
            def ln(name, x):
                if not x["closed"] and not x["expired"]:
                    return f"{name}: ما فيه صفقات"
                wr = f" ({x['win_rate']}%)" if x["win_rate"] is not None else ""
                return (f"{name}: {x['closed']} صفقة، فوز {x['wins']}{wr}، مجموع {x['total_r']}R، "
                        f"${x['pnl']} • ما تفعّلت {x['expired']}")
            txt = "\n".join([f"📊 ملخص مكتب التداول — {day}",
                             f"أفكار: {len(ideas)} (شراء {self.L.d['daily'][day]['buy']}، انتظار {self.L.d['daily'][day]['wait']}، أوقفها Skeptic {self.L.d['daily'][day]['blocked']})",
                             ln("🏛️ المكتب", s["desk"]), ln("👤 أنت", s["you"]),
                             ln("🛡️ اللي أوقفها Skeptic (لو دخلناها)", s["blocked"]),
                             "إذا الأخير بالسالب = Skeptic أنقذنا.", self.cfg.get("site_url", "")])
            try:
                self.tg.send(txt)
            except Exception as e:
                self.errors.append(f"تيليجرام ملخص: {str(e)[:80]}")
