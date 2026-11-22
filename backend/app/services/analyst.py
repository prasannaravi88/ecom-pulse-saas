"""Guardrailed analyst: maps executive questions onto canonical, pre-aggregated rollups.

There is no SQL generation and no write path. A question is routed to one of a
fixed set of intents by keyword scoring; each intent reads AnalyticsEngine
outputs (the same numbers the dashboard shows) and composes a narrative, a few
headline figures and a supporting table. Anything that looks like a data
modification request, or that matches no intent, is refused with suggestions.

Money in `highlights` and `table` rows stays in the tenant's own currency (the
UI converts it with the rest of the dashboard); only the narrative text is
rendered in the requested display currency.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from typing import Callable

from app.services import finance as fin
from app.services.analytics import AnalyticsEngine, Period

MAX_QUESTION_CHARS = 500
WRITE_VERBS = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|grant|revoke|remove|overwrite|modify|edit|wipe|"
    r"purge|reset|set|change)\b", re.IGNORECASE)
DATA_NOUNS = re.compile(
    r"\b(table|tables|row|rows|record|records|database|db|schema|order|orders|data|sku|skus|price|"
    r"prices|cogs|spend|campaign|campaigns|tenant|tenants|customer|customers|value|values)\b", re.IGNORECASE)
SQL_PATTERN = re.compile(r";|--|/\*|\bselect\b.+\bfrom\b|\bunion\b.+\bselect\b|\bexec(ute)?\b",
                         re.IGNORECASE | re.DOTALL)


def is_write_attempt(question: str) -> bool:
    """Raw SQL, or a modification verb aimed at data. 'What changed in CM2?' is fine."""
    if SQL_PATTERN.search(question):
        return True
    return bool(WRITE_VERBS.search(question) and DATA_NOUNS.search(question)
                and not re.match(r"\s*(what|why|how|which|did|has|have)\b", question, re.IGNORECASE))


GUARDRAIL_NOTE = ("Read-only: answered from canonical pre-aggregated rollups "
                  "(net revenue, CM1/CM2, True ROAS, cohorts). No SQL was generated or executed.")


@dataclass(frozen=True)
class Intent:
    id: str
    question: str
    keywords: frozenset[str]
    build: Callable[["Ctx"], dict]


class Ctx:
    def __init__(self, engine: AnalyticsEngine, period: Period, tenant_currency: str,
                 display_currency: str, usd_inr: float):
        self.engine = engine
        self.period = period
        self.tenant_currency = tenant_currency
        self.display_currency = display_currency
        self.usd_inr = usd_inr

    @property
    def window(self) -> str:
        return f"{self.period.start:%d %b} – {self.period.end:%d %b %Y}"

    def money(self, value: float | None) -> str:
        if value is None:
            return "n/a"
        if self.tenant_currency == "INR" and self.display_currency == "USD":
            value = value / self.usd_inr
        elif self.tenant_currency == "USD" and self.display_currency == "INR":
            value = value * self.usd_inr
        return format_money(value, self.display_currency)


def format_money(value: float, currency: str) -> str:
    sign = "−" if value < 0 else ""
    v = abs(value)
    if currency == "INR":
        if v >= 1e7:
            return f"{sign}₹{v / 1e7:.2f} Cr"
        if v >= 1e5:
            return f"{sign}₹{v / 1e5:.2f} L"
        return f"{sign}₹{v:,.0f}"
    if v >= 1e6:
        return f"{sign}${v / 1e6:.2f}M"
    if v >= 1e4:
        return f"{sign}${v / 1e3:.1f}K"
    return f"{sign}${v:,.2f}" if v < 100 else f"{sign}${v:,.0f}"


def pct(value: float | None, digits: int = 0) -> str:
    return "n/a" if value is None else f"{value * 100:.{digits}f}%"


def ratio(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}x"


def _col(key: str, label: str, fmt: str = "text") -> dict:
    return {"key": key, "label": label, "format": fmt}


# --------------------------------------------------------------------------- intents


def _margin_leaks(ctx: Ctx) -> dict:
    skus = ctx.engine.skus(ctx.period.start, ctx.period.end)["skus"]
    total_cm2 = ctx.engine.pnl(ctx.period.start, ctx.period.end)["cm2"]
    leaks = sorted((s for s in skus if s["segment"] == "margin_leak"), key=lambda s: s["cm2"])
    if not leaks:
        return {
            "title": "No SKUs are leaking margin",
            "summary": [f"None of the {len(skus)} SKUs trip the margin-leak guardrails for {ctx.window}."],
            "highlights": [{"label": "Tenant CM2", "value": total_cm2, "format": "money"}],
            "table": None, "actions": [],
        }
    negative = [s for s in leaks if s["cm2"] < 0]
    drag = -sum(s["cm2"] for s in negative)
    worst = leaks[0]
    summary = [
        f"{len(leaks)} of {len(skus)} SKUs are flagged as margin leaks for {ctx.window}; "
        f"{len(negative)} of them have negative CM2, dragging contribution by {ctx.money(drag)}.",
        f"Worst: {worst['name']}, CM2 {ctx.money(worst['cm2'])} ({pct(worst['cm2_pct'])} of its net revenue) "
        f"with {pct(worst['return_rate'])} of sales reversed as refunds/RTO and ad spend at "
        f"{pct(worst['ad_to_revenue'])} of net revenue.",
    ]
    if negative:
        summary.append(f"Before halo effects, fixing the negative-CM2 SKUs would move tenant CM2 from "
                       f"{ctx.money(total_cm2)} to {ctx.money(total_cm2 + drag)}.")
    actions = []
    for s in leaks[:3]:
        if (s["return_rate"] or 0) >= 0.2:
            actions.append(f"{s['sku']}: gate COD (OTP/partial prepaid) and review listing claims; "
                           f"{pct(s['return_rate'])} of sales come back.")
        if (s["ad_to_revenue"] or 0) >= 0.5:
            actions.append(f"{s['sku']}: cap prospecting spend; True ROAS is {ratio(s['true_roas'])}.")
    return {
        "title": f"{len(leaks)} SKUs leaking contribution margin",
        "summary": summary,
        "highlights": [
            {"label": "Leaking SKUs", "value": len(leaks), "format": "int", "tone": "risk"},
            {"label": "CM2 drag", "value": -drag, "format": "money", "tone": "risk"},
            {"label": "Tenant CM2", "value": total_cm2, "format": "money"},
        ],
        "table": {
            "columns": [_col("name", "SKU"), _col("net_revenue", "Net revenue", "money"),
                        _col("ad_spend", "Ad spend", "money"), _col("return_rate", "Refund/RTO", "pct"),
                        _col("cm2", "CM2", "money"), _col("cm2_pct", "CM2 %", "pct")],
            "rows": leaks,
        },
        "actions": actions,
    }


def _festive_payback(ctx: Ctx) -> dict:
    cohorts = ctx.engine.cohorts()["cohorts"]
    if len(cohorts) < 2:
        return {"title": "Not enough cohort history", "summary": ["Need at least two acquisition months."],
                "highlights": [], "table": None, "actions": []}
    median_size = statistics.median(c["size"] for c in cohorts)
    rows = []
    for c in cohorts:
        last = next((x for x in reversed(c["cells"]) if x["available"]), None)
        months_observed = sum(1 for x in c["cells"] if x["available"])
        festive = c["size"] >= 1.3 * median_size
        recovered = fin.safe_div(last["ltv_contribution"], c["cac"]) if last and c["cac"] else None
        rows.append({
            "cohort_month": c["cohort_month"], "segment": "festive" if festive else "regular",
            "size": c["size"], "cac": c["cac"], "ltv_contribution": last["ltv_contribution"] if last else None,
            "cac_recovered": recovered, "payback_month": c["payback_month"],
            "months_observed": months_observed,
        })
    festive = [r for r in rows if r["segment"] == "festive"]
    regular = [r for r in rows if r["segment"] == "regular"]
    if not festive:
        return {"title": "No festive cohort detected",
                "summary": ["No acquisition month is at least 1.3x the median cohort size."],
                "highlights": [], "table": None, "actions": []}

    def avg(values):
        values = [v for v in values if v is not None]
        return sum(values) / len(values) if values else None

    reg_payback = avg(r["payback_month"] for r in regular)
    reg_cac, fest_cac = avg(r["cac"] for r in regular), avg(r["cac"] for r in festive)
    # Same-age comparison: CAC recovered by the end of M1 (or M0 when that's all we have).
    def recovered_at(month_iso: str, m: int):
        c = next(c for c in cohorts if c["cohort_month"] == month_iso)
        cell = c["cells"][m]
        return fin.safe_div(cell["ltv_contribution"], c["cac"]) if cell["available"] and c["cac"] else None

    m = 1 if all(r["months_observed"] >= 2 for r in festive[:1]) else 0
    fest_rec = avg(recovered_at(r["cohort_month"], m) for r in festive)
    reg_rec = avg(recovered_at(r["cohort_month"], m) for r in regular)
    names = ", ".join(f"{r['cohort_month'][:7]}" for r in festive)
    summary = [
        f"Festive cohorts ({names}) were {festive[0]['size'] / median_size:.1f}x a normal month's size but cost "
        f"{ctx.money(fest_cac)} to acquire vs {ctx.money(reg_cac)} for regular cohorts "
        f"({pct(fin.pct_change(fest_cac, reg_cac))} higher).",
        f"By M{m} they had recovered {pct(fest_rec)} of CAC from contribution, vs {pct(reg_rec)} for regular cohorts at the same age.",
    ]
    paid = [r for r in festive if r["payback_month"] is not None]
    if reg_payback is not None:
        summary.append(f"Regular cohorts pay back in M{reg_payback:.1f} on average; "
                       + (f"festive cohorts in M{avg(r['payback_month'] for r in paid):.1f}."
                          if paid else "no festive cohort has paid back yet."))
    return {
        "title": "Festive cohorts pay back slower",
        "summary": summary,
        "highlights": [
            {"label": "Festive CAC", "value": fest_cac, "format": "money", "tone": "risk"},
            {"label": "Regular CAC", "value": reg_cac, "format": "money"},
            {"label": f"Festive CAC recovered by M{m}", "value": fest_rec, "format": "pct"},
            {"label": f"Regular CAC recovered by M{m}", "value": reg_rec, "format": "pct", "tone": "positive"},
        ],
        "table": {
            "columns": [_col("cohort_month", "Cohort", "month"), _col("segment", "Type"),
                        _col("size", "Customers", "int"), _col("cac", "CAC", "money"),
                        _col("ltv_contribution", "Contribution LTV", "money"),
                        _col("cac_recovered", "CAC recovered", "pct"), _col("payback_month", "Payback", "month_offset")],
            "rows": rows,
        },
        "actions": ["Plan festive budgets on M2–M3 payback, not M1, and push post-festive replenishment "
                    "flows to the festive cohorts to lift their M1–M2 repeat rate."],
    }


def _channel_roas(ctx: Ctx) -> dict:
    channels = [c for c in ctx.engine.channels(ctx.period.start, ctx.period.end) if c["ad_spend"]]
    if not channels:
        return {"title": "No paid spend in this window", "summary": [f"No ad spend recorded for {ctx.window}."],
                "highlights": [], "table": None, "actions": []}
    summary = []
    for c in channels:
        summary.append(
            f"{c['channel'].title()}: True ROAS {ratio(c['true_roas'])} on {ctx.money(c['ad_spend'])} spend, vs "
            f"{ratio(c['platform_roas'])} reported by the platform ({ratio(c['roas_inflation'])} inflated). "
            f"{ctx.money(c['refunds_rto'])} of its attributed sales were reversed as refunds/RTO.")
    best = max(channels, key=lambda c: c["true_roas"] or 0)
    others = [c for c in channels if c is not best]
    if others:
        o = others[0]
        summary.append(f"{best['channel'].title()} returns {(best['true_roas'] or 0) - (o['true_roas'] or 0):.2f} "
                       f"more net revenue per 1 of ad spend than {o['channel'].title()}, at a CAC of "
                       f"{ctx.money(best['cac'])} vs {ctx.money(o['cac'])}.")
    return {
        "title": f"{best['channel'].title()} leads on True ROAS",
        "summary": summary,
        "highlights": [h for c in channels for h in (
            {"label": f"{c['channel'].title()} True ROAS", "value": c["true_roas"], "format": "ratio",
             "tone": "positive" if c is best else None},
            {"label": f"{c['channel'].title()} platform ROAS", "value": c["platform_roas"], "format": "ratio"},
        )],
        "table": {
            "columns": [_col("channel", "Channel"), _col("ad_spend", "Spend", "money"),
                        _col("platform_roas", "Platform ROAS", "ratio"), _col("true_roas", "True ROAS", "ratio"),
                        _col("roas_inflation", "Inflation", "ratio"), _col("refunds_rto", "Refunds/RTO", "money"),
                        _col("new_customers", "New customers", "int"), _col("cac", "CAC", "money")],
            "rows": channels,
        },
        "actions": ["Budget on True ROAS and CAC payback, not platform ROAS; platform numbers count RTO'd and refunded orders."],
    }


def _cm2_bridge(ctx: Ctx) -> dict:
    cur = ctx.engine.pnl(ctx.period.start, ctx.period.end)
    prev = ctx.engine.pnl(ctx.period.prev_start, ctx.period.prev_end)
    # CM2 = Net Revenue - COGS - (Shipping + Fees) - Ad Spend, so the deltas add up exactly.
    steps = [
        ("Net revenue", cur["net_revenue"] - prev["net_revenue"]),
        ("COGS", -(cur["cogs"] - prev["cogs"])),
        ("Shipping & fees", -((cur["shipping"] + cur["payment_fees"]) - (prev["shipping"] + prev["payment_fees"]))),
        ("Ad spend", -(cur["ad_spend"] - prev["ad_spend"])),
    ]
    delta = cur["cm2"] - prev["cm2"]
    driver = max(steps, key=lambda s: abs(s[1]))
    direction = "rose" if delta >= 0 else "fell"
    summary = [
        f"CM2 {direction} from {ctx.money(prev['cm2'])} ({pct(prev['cm2_pct'], 1)}) to {ctx.money(cur['cm2'])} "
        f"({pct(cur['cm2_pct'], 1)}) for {ctx.window} vs the previous {ctx.period.days} days.",
        "Bridge: " + "; ".join(f"{name} {'+' if v >= 0 else ''}{ctx.money(v)}" for name, v in steps) + ".",
        f"Largest driver: {driver[0].lower()} ({'+' if driver[1] >= 0 else ''}{ctx.money(driver[1])} to CM2). "
        f"MER moved {ratio(prev['blended_mer'])} → {ratio(cur['blended_mer'])}, CAC "
        f"{ctx.money(prev['blended_cac'])} → {ctx.money(cur['blended_cac'])}.",
    ]
    return {
        "title": f"CM2 {direction} {ctx.money(abs(delta))} vs previous period",
        "summary": summary,
        "highlights": [
            {"label": "CM2 now", "value": cur["cm2"], "format": "money", "tone": "positive" if cur["cm2"] >= 0 else "risk"},
            {"label": "CM2 before", "value": prev["cm2"], "format": "money"},
            {"label": "Change", "value": delta, "format": "money", "tone": "positive" if delta >= 0 else "risk"},
        ],
        "table": {
            "columns": [_col("component", "Component"), _col("impact", "Impact on CM2", "money")],
            "rows": [{"component": n, "impact": v} for n, v in steps] + [{"component": "CM2 change", "impact": delta}],
        },
        "actions": [],
    }


def _cod_rto(ctx: Ctx) -> dict:
    mix = {m["payment_method"]: m for m in ctx.engine.payment_mix(ctx.period.start, ctx.period.end)}
    cod, prepaid = mix.get("cod"), mix.get("prepaid")
    total_rto = sum(m["rto_orders"] for m in mix.values())
    rto_cost = sum(m["rto_logistics_cost"] for m in mix.values())
    rto_rev = sum(m["rto_reversed_revenue"] for m in mix.values())
    if not cod:
        return {
            "title": "No COD orders in this window",
            "summary": [f"All orders are prepaid; {total_rto} RTOs cost {ctx.money(rto_cost)} in logistics."],
            "highlights": [{"label": "RTO logistics cost", "value": rto_cost, "format": "money"}],
            "table": None, "actions": [],
        }
    cod_rto_share = fin.safe_div(cod["rto_orders"], total_rto)
    summary = [
        f"COD is {pct(cod['order_share'])} of orders but {pct(cod_rto_share)} of RTOs for {ctx.window}: "
        f"{pct(cod['rto_rate'], 1)} of COD orders come back vs {pct(prepaid['rto_rate'] if prepaid else None, 1)} prepaid.",
        f"RTOs reversed {ctx.money(rto_rev)} of sales and burned {ctx.money(rto_cost)} in forward + return "
        f"shipping and fees, of which COD accounts for {ctx.money(cod['rto_logistics_cost'])}.",
    ]
    if prepaid and prepaid["rto_rate"] is not None and cod["rto_rate"]:
        avoidable = cod["rto_logistics_cost"] * (1 - prepaid["rto_rate"] / cod["rto_rate"])
        summary.append(f"Bringing COD RTO down to the prepaid rate would save about {ctx.money(avoidable)} "
                       f"in logistics alone over this window.")
    return {
        "title": f"COD RTO is costing {ctx.money(cod['rto_logistics_cost'])} in logistics",
        "summary": summary,
        "highlights": [
            {"label": "COD RTO rate", "value": cod["rto_rate"], "format": "pct", "tone": "risk"},
            {"label": "Prepaid RTO rate", "value": prepaid["rto_rate"] if prepaid else None, "format": "pct"},
            {"label": "RTO logistics cost", "value": rto_cost, "format": "money", "tone": "risk"},
        ],
        "table": {
            "columns": [_col("payment_method", "Payment"), _col("orders", "Orders", "int"),
                        _col("order_share", "Share", "pct"), _col("rto_rate", "RTO rate", "pct"),
                        _col("rto_reversed_revenue", "Reversed sales", "money"),
                        _col("rto_logistics_cost", "RTO logistics", "money")],
            "rows": list(mix.values()),
        },
        "actions": ["Add COD confirmation (IVR/WhatsApp) and a prepaid discount nudge on RTO-prone SKUs and pincodes."],
    }


def _hero_skus(ctx: Ctx) -> dict:
    skus = ctx.engine.skus(ctx.period.start, ctx.period.end)["skus"]
    heroes = sorted((s for s in skus if s["segment"] == "hero"), key=lambda s: -s["cm2"])
    if not heroes:
        return {"title": "No hero SKUs this window",
                "summary": ["No SKU combines a high repeat rate with healthy CM2 for " + ctx.window + "."],
                "highlights": [], "table": None, "actions": []}
    share = fin.safe_div(sum(h["cm2"] for h in heroes), sum(s["cm2"] for s in skus if s["cm2"] > 0))
    top = heroes[0]
    return {
        "title": f"{len(heroes)} hero SKUs carry the margin",
        "summary": [
            f"{len(heroes)} SKUs combine repeat purchase with healthy CM2 and generate {pct(share)} of all positive CM2 for {ctx.window}.",
            f"Top: {top['name']}, CM2 {ctx.money(top['cm2'])} ({pct(top['cm2_pct'])}), repeat rate "
            f"{pct(top['repeat_rate'])}, True ROAS {ratio(top['true_roas'])}.",
        ],
        "highlights": [
            {"label": "Hero SKUs", "value": len(heroes), "format": "int", "tone": "positive"},
            {"label": "Share of positive CM2", "value": share, "format": "pct", "tone": "positive"},
        ],
        "table": {
            "columns": [_col("name", "SKU"), _col("net_revenue", "Net revenue", "money"),
                        _col("repeat_rate", "Repeat rate", "pct"), _col("cm2", "CM2", "money"),
                        _col("cm2_pct", "CM2 %", "pct"), _col("true_roas", "True ROAS", "ratio")],
            "rows": heroes,
        },
        "actions": [f"Shift prospecting budget from leak SKUs towards {', '.join(h['sku'] for h in heroes[:3])} "
                    "and use them as entry products in acquisition bundles."],
    }


INTENTS = [
    Intent("margin_leaks", "Which SKUs are leaking contribution margin this month?",
           frozenset("leak leaks leaking leakage bleeding losing loss sku skus product products negative worst".split()),
           _margin_leaks),
    Intent("festive_payback", "How fast do festive cohorts pay back CAC?",
           frozenset("festive festival diwali bfcm holiday cohort cohorts payback pay back ltv recover recovered".split()),
           _festive_payback),
    Intent("channel_roas", "Compare Meta vs. Google True ROAS after refunds",
           frozenset("meta google facebook instagram roas channel channels platform attribution ads true".split()),
           _channel_roas),
    Intent("cm2_bridge", "What drove the change in contribution margin vs. last period?",
           frozenset("drove driver drivers change changed why bridge previous last period drop dropped decline "
                     "increase improve cm2 contribution".split()),
           _cm2_bridge),
    Intent("cod_rto", "How much is COD RTO costing us?",
           frozenset("cod rto cash delivery prepaid origin returned returns payment".split()),
           _cod_rto),
    Intent("hero_skus", "Which hero SKUs deserve more budget?",
           frozenset("hero heroes best winners winning scale budget top repeat deserve invest".split()),
           _hero_skus),
]
INTENTS_BY_ID = {i.id: i for i in INTENTS}


def prompts() -> list[dict]:
    return [{"id": i.id, "question": i.question} for i in INTENTS]


def route(question: str) -> Intent | None:
    tokens = set(re.findall(r"[a-z0-9]+", question.lower()))
    scored = sorted(((len(tokens & i.keywords), n, i) for n, i in enumerate(INTENTS)),
                    key=lambda t: (-t[0], t[1]))
    best_score, _, best = scored[0]
    return best if best_score >= 1 else None


def ask(ctx: Ctx, question: str | None = None, prompt_id: str | None = None) -> dict:
    base = {"guardrail": GUARDRAIL_NOTE, "period": {"start": ctx.period.start.isoformat(),
                                                    "end": ctx.period.end.isoformat()},
            "display_currency": ctx.display_currency}
    if prompt_id:
        intent = INTENTS_BY_ID.get(prompt_id)
        if intent is None:
            return base | {"status": "unsupported", "intent": None, "question": prompt_id,
                           "title": "Unknown prompt", "summary": ["That prompt id doesn't exist."],
                           "suggestions": prompts()}
        question = intent.question
    question = (question or "").strip()
    if not question:
        return base | {"status": "unsupported", "intent": None, "question": "", "title": "Ask a question",
                       "summary": ["Pick one of the suggested questions or type your own."], "suggestions": prompts()}
    if len(question) > MAX_QUESTION_CHARS:
        return base | {"status": "refused", "intent": None, "question": question[:80] + "…",
                       "title": "Question too long", "summary": [f"Keep questions under {MAX_QUESTION_CHARS} characters."],
                       "suggestions": prompts()}
    if is_write_attempt(question):
        return base | {"status": "refused", "intent": None, "question": question,
                       "title": "Read-only analyst",
                       "summary": ["I can only read the dashboard metrics; I can't modify, delete or run queries against data. "
                                   "Use the Upload CSV drawer to change data."],
                       "suggestions": prompts()}
    intent = INTENTS_BY_ID[prompt_id] if prompt_id else route(question)
    if intent is None:
        return base | {"status": "unsupported", "intent": None, "question": question,
                       "title": "Outside what I can answer",
                       "summary": ["I answer questions about margin leaks, festive cohort payback, Meta vs Google "
                                   "True ROAS, CM2 drivers, COD/RTO cost and hero SKUs."],
                       "suggestions": prompts()}
    answer = intent.build(ctx)
    return base | {"status": "answered", "intent": intent.id, "question": question,
                   "sources": SOURCES[intent.id], **answer}


SOURCES = {
    "margin_leaks": ["GET /api/metrics/skus", "GET /api/metrics/overview"],
    "festive_payback": ["GET /api/cohorts"],
    "channel_roas": ["GET /api/metrics/channels"],
    "cm2_bridge": ["GET /api/metrics/overview"],
    "cod_rto": ["GET /api/metrics/payment-mix"],
    "hero_skus": ["GET /api/metrics/skus"],
}
