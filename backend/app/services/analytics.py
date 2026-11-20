"""DuckDB-backed analytics engine for one tenant.

Flow: `load_tenant_frames` pulls ONLY the requesting tenant's raw facts from the
relational DB (every query filters on tenant_id), then `AnalyticsEngine` loads
them into an in-memory DuckDB database and runs the canonical rollups.

Accounting conventions (applied consistently everywhere):
  * `cancelled` orders never shipped: excluded entirely.
  * `rto` (return to origin, mostly COD refusals): the full line value
    (gross - discount) is reversed as Refunds/RTO; goods return to stock, so
    COGS is 0, but forward + reverse shipping and fees are still incurred.
  * `refunded`: the stored line refund_amount is reversed; goods are written
    off, so COGS stays.
  * Order-level shipping and payment fees are allocated to lines pro-rata by
    line gross value (equal split if the order gross is 0).
  * A customer is "active" in a month if they placed a non-RTO order then.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

import duckdb
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DailyAdSpend, Order, OrderItem, Product
from app.services import finance as fin

PeriodPreset = Literal["7D", "30D", "90D", "QTD"]
COHORT_MAX_OFFSET = 6  # M0..M6

# SKU guardrail thresholds (returned with the payload so the UI can explain them).
HERO_MIN_REPEAT_RATE = 0.12
HERO_MIN_CM2_PCT = 0.15
LEAK_MIN_RETURN_RATE = 0.20
LEAK_MIN_AD_TO_REVENUE = 0.30

LINE_COLUMNS = [
    "order_id", "order_ref", "order_date", "customer_ref", "status", "payment_method",
    "channel", "shipping_cost", "payment_fee", "sku", "quantity", "unit_price", "discount",
    "refund_amount", "unit_cogs",
]
SPEND_COLUMNS = [
    "spend_date", "channel", "campaign", "sku", "spend", "impressions", "clicks",
    "platform_reported_revenue",
]
PRODUCT_COLUMNS = ["sku", "name", "category", "price", "unit_cogs"]

_DDL = """
CREATE TABLE raw_lines (
    order_id BIGINT, order_ref VARCHAR, order_date DATE, customer_ref VARCHAR, status VARCHAR,
    payment_method VARCHAR, channel VARCHAR, shipping_cost DOUBLE, payment_fee DOUBLE,
    sku VARCHAR, quantity INTEGER, unit_price DOUBLE, discount DOUBLE, refund_amount DOUBLE,
    unit_cogs DOUBLE
);
CREATE TABLE spend (
    spend_date DATE, channel VARCHAR, campaign VARCHAR, sku VARCHAR, spend DOUBLE,
    impressions BIGINT, clicks BIGINT, platform_reported_revenue DOUBLE
);
CREATE TABLE products (sku VARCHAR, name VARCHAR, category VARCHAR, price DOUBLE, unit_cogs DOUBLE);

CREATE VIEW fact_lines AS
WITH base AS (
    SELECT *,
        quantity * unit_price AS gross,
        SUM(quantity * unit_price) OVER (PARTITION BY order_id) AS order_gross,
        COUNT(*) OVER (PARTITION BY order_id) AS order_lines
    FROM raw_lines
    WHERE status <> 'cancelled'
), shared AS (
    SELECT *,
        CASE WHEN order_gross > 0 THEN gross / order_gross ELSE 1.0 / order_lines END AS share
    FROM base
)
SELECT
    order_id, order_ref, order_date, customer_ref, status, payment_method, channel, sku, quantity,
    gross,
    discount,
    CASE WHEN status = 'rto' THEN gross - discount ELSE refund_amount END AS returns,
    CASE WHEN status = 'rto' THEN 0.0 ELSE quantity * unit_cogs END AS cogs,
    shipping_cost * share AS shipping,
    payment_fee * share AS fees
FROM shared;

CREATE VIEW fact_orders AS
SELECT
    order_id,
    any_value(order_date) AS order_date,
    any_value(customer_ref) AS customer_ref,
    any_value(status) AS status,
    any_value(payment_method) AS payment_method,
    any_value(channel) AS channel,
    SUM(gross) AS gross, SUM(discount) AS discount, SUM(returns) AS returns,
    SUM(cogs) AS cogs, SUM(shipping) AS shipping, SUM(fees) AS fees
FROM fact_lines
GROUP BY order_id;

CREATE VIEW dim_customers AS
SELECT
    customer_ref,
    MIN(order_date) AS first_order_date,
    CAST(date_trunc('month', MIN(order_date)) AS DATE) AS cohort_month,
    arg_min(channel, order_date) AS acquisition_channel
FROM fact_orders
GROUP BY customer_ref;
"""


# --------------------------------------------------------------------------- loading


@dataclass
class TenantFrames:
    lines: pd.DataFrame
    spend: pd.DataFrame
    products: pd.DataFrame


def load_tenant_frames(session: Session, tenant_id: str) -> TenantFrames:
    """Fetch one tenant's raw facts. tenant_id is applied to every table touched."""
    # Two single-table scans merged in pandas instead of a SQL join: a freshly cloned or
    # seeded tenant has no planner statistics yet, and Postgres would pick a quadratic
    # nested loop for the join (minutes for ~40k lines). Each scan below is index-bounded.
    order_rows = session.execute(
        select(
            Order.id, Order.order_ref, Order.order_date, Order.customer_ref, Order.status,
            Order.payment_method, Order.attributed_channel, Order.shipping_cost, Order.payment_fee,
        ).where(Order.tenant_id == tenant_id)
    ).all()
    item_rows = session.execute(
        select(
            OrderItem.order_id, OrderItem.sku, OrderItem.quantity, OrderItem.unit_price,
            OrderItem.discount, OrderItem.refund_amount, OrderItem.unit_cogs,
        ).where(OrderItem.tenant_id == tenant_id)
    ).all()
    orders_df = pd.DataFrame(order_rows, columns=LINE_COLUMNS[:9])
    items_df = pd.DataFrame(item_rows, columns=["order_id", *LINE_COLUMNS[9:]])
    lines = orders_df.merge(items_df, on="order_id", how="inner")[LINE_COLUMNS]
    spend_rows = session.execute(
        select(
            DailyAdSpend.spend_date, DailyAdSpend.channel, DailyAdSpend.campaign,
            DailyAdSpend.sku, DailyAdSpend.spend, DailyAdSpend.impressions,
            DailyAdSpend.clicks, DailyAdSpend.platform_reported_revenue,
        ).where(DailyAdSpend.tenant_id == tenant_id)
    ).all()
    product_rows = session.execute(
        select(Product.sku, Product.name, Product.category, Product.price, Product.unit_cogs)
        .where(Product.tenant_id == tenant_id)
    ).all()
    return TenantFrames(
        lines=lines,
        spend=pd.DataFrame(spend_rows, columns=SPEND_COLUMNS),
        products=pd.DataFrame(product_rows, columns=PRODUCT_COLUMNS),
    )


def _coerce(df: pd.DataFrame, money: list[str]) -> pd.DataFrame:
    df = df.copy()
    for col in money:
        df[col] = df[col].astype(float)
    return df


# --------------------------------------------------------------------------- helpers


def _num(value) -> float:
    """DuckDB NULL / NaN -> 0.0 for summed money."""
    if value is None:
        return 0.0
    value = float(value)
    return 0.0 if math.isnan(value) else value


def _r(value: float | None, digits: int = 2) -> float | None:
    if value is None:
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return round(float(value), digits)


def _month_add(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    return date(d.year + y, m + 1, 1)


@dataclass(frozen=True)
class Period:
    start: date
    end: date
    prev_start: date
    prev_end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


def resolve_period(preset: PeriodPreset, as_of: date) -> Period:
    if preset == "QTD":
        q_start = date(as_of.year, 3 * ((as_of.month - 1) // 3) + 1, 1)
        prev_q_start = _month_add(q_start, -3)
        elapsed = as_of - q_start
        prev_end = min(prev_q_start + elapsed, q_start - timedelta(days=1))
        return Period(q_start, as_of, prev_q_start, prev_end)
    days = {"7D": 7, "30D": 30, "90D": 90}[preset]
    start = as_of - timedelta(days=days - 1)
    return Period(start, as_of, start - timedelta(days=days), start - timedelta(days=1))


def custom_period(start: date, end: date) -> Period:
    length = (end - start).days + 1
    return Period(start, end, start - timedelta(days=length), start - timedelta(days=1))


# --------------------------------------------------------------------------- engine


class AnalyticsEngine:
    def __init__(self, frames: TenantFrames):
        self._lock = threading.Lock()  # one DuckDB connection, shared across request threads
        self.con = duckdb.connect(database=":memory:")
        self.con.execute(_DDL)
        lines = _coerce(
            frames.lines,
            ["shipping_cost", "payment_fee", "unit_price", "discount", "refund_amount", "unit_cogs"],
        )
        spend = _coerce(frames.spend, ["spend", "platform_reported_revenue"])
        products = _coerce(frames.products, ["price", "unit_cogs"])
        for table, df in (("raw_lines", lines), ("spend", spend), ("products", products)):
            if len(df):
                self.con.register("_src", df)
                self.con.execute(f"INSERT INTO {table} SELECT * FROM _src")
                self.con.unregister("_src")

    @classmethod
    def for_tenant(cls, session: Session, tenant_id: str) -> "AnalyticsEngine":
        return cls(load_tenant_frames(session, tenant_id))

    def close(self) -> None:
        self.con.close()

    def _one(self, sql: str, params: list | None = None) -> tuple:
        with self._lock:
            return self.con.execute(sql, params or []).fetchone()

    def _df(self, sql: str, params: list | None = None) -> pd.DataFrame:
        with self._lock:
            return self.con.execute(sql, params or []).df()

    # ------------------------------------------------------------------ dates

    def data_bounds(self) -> tuple[date | None, date | None]:
        lo, hi = self._one(
            """SELECT MIN(d), MAX(d) FROM (
                   SELECT order_date AS d FROM fact_lines
                   UNION ALL SELECT spend_date FROM spend)"""
        )
        return lo, hi

    def as_of(self) -> date:
        return self.data_bounds()[1] or date.today()

    # ------------------------------------------------------------------ P&L

    def pnl(self, start: date, end: date) -> dict:
        """Canonical P&L for [start, end] (inclusive)."""
        gross, discounts, returns, cogs, shipping, fees, orders = self._one(
            """SELECT SUM(gross), SUM(discount), SUM(returns), SUM(cogs), SUM(shipping),
                      SUM(fees), COUNT(DISTINCT order_id)
               FROM fact_lines WHERE order_date BETWEEN ? AND ?""",
            [start, end],
        )
        rto_orders, refunded_orders, cod_orders = self._one(
            """SELECT COUNT(*) FILTER (status = 'rto'), COUNT(*) FILTER (status = 'refunded'),
                      COUNT(*) FILTER (payment_method = 'cod')
               FROM fact_orders WHERE order_date BETWEEN ? AND ?""",
            [start, end],
        )
        (ad_spend,) = self._one(
            "SELECT SUM(spend) FROM spend WHERE spend_date BETWEEN ? AND ?", [start, end]
        )
        (new_customers,) = self._one(
            "SELECT COUNT(*) FROM dim_customers WHERE first_order_date BETWEEN ? AND ?",
            [start, end],
        )
        gross, discounts, returns = _num(gross), _num(discounts), _num(returns)
        cogs, shipping, fees, ad_spend = _num(cogs), _num(shipping), _num(fees), _num(ad_spend)
        orders = int(orders or 0)

        net = fin.net_revenue(gross, discounts, returns)
        cm1 = fin.cm1(net, cogs)
        cm2 = fin.cm2(cm1, shipping, fees, ad_spend)
        return {
            "gross_revenue": gross,
            "discounts": discounts,
            "refunds_rto": returns,
            "net_revenue": net,
            "cogs": cogs,
            "cm1": cm1,
            "cm1_pct": fin.safe_div(cm1, net),
            "shipping": shipping,
            "payment_fees": fees,
            "ad_spend": ad_spend,
            "cm2": cm2,
            "cm2_pct": fin.safe_div(cm2, net),
            "blended_mer": fin.blended_mer(net, ad_spend),
            "new_customers": int(new_customers or 0),
            "blended_cac": fin.blended_cac(ad_spend, int(new_customers or 0)),
            "orders": orders,
            "aov": fin.safe_div(net, orders),
            "rto_rate": fin.safe_div(int(rto_orders or 0), orders),
            "refund_rate": fin.safe_div(int(refunded_orders or 0), orders),
            "cod_share": fin.safe_div(int(cod_orders or 0), orders),
        }

    def overview(self, period: Period) -> dict:
        cur = self.pnl(period.start, period.end)
        prev = self.pnl(period.prev_start, period.prev_end)
        kpis = {}
        for key, value in cur.items():
            digits = 4 if key.endswith(("_pct", "_rate", "_share", "_mer")) else 2
            kpis[key] = {
                "value": _r(value, digits),
                "previous": _r(prev[key], digits),
                "delta_pct": _r(fin.pct_change(value, prev[key]), 4),
            }
        waterfall = [
            {"step": "Gross Sales", "kind": "total", "value": cur["gross_revenue"]},
            {"step": "Discounts", "kind": "decrease", "value": -cur["discounts"]},
            {"step": "Refunds / RTO", "kind": "decrease", "value": -cur["refunds_rto"]},
            {"step": "Net Revenue", "kind": "subtotal", "value": cur["net_revenue"]},
            {"step": "COGS", "kind": "decrease", "value": -cur["cogs"]},
            {"step": "CM1", "kind": "subtotal", "value": cur["cm1"]},
            {"step": "Shipping & Fees", "kind": "decrease",
             "value": -(cur["shipping"] + cur["payment_fees"])},
            {"step": "Ad Spend", "kind": "decrease", "value": -cur["ad_spend"]},
            {"step": "Net Contribution (CM2)", "kind": "total", "value": cur["cm2"]},
        ]
        for step in waterfall:
            step["value"] = _r(step["value"])
        return {"kpis": kpis, "waterfall": waterfall}

    def daily(self, start: date, end: date) -> list[dict]:
        df = self._df(
            """WITH days AS (
                   SELECT CAST(d AS DATE) AS day FROM generate_series(?::DATE, ?::DATE, INTERVAL 1 DAY) t(d)
               ), l AS (
                   SELECT order_date AS day, SUM(gross) gross, SUM(discount) discounts,
                          SUM(returns) refunds_rto, SUM(cogs) cogs, SUM(shipping) shipping,
                          SUM(fees) fees, COUNT(DISTINCT order_id) orders
                   FROM fact_lines WHERE order_date BETWEEN ? AND ? GROUP BY 1
               ), s AS (
                   SELECT spend_date AS day, SUM(spend) ad_spend,
                          SUM(spend) FILTER (channel = 'meta') meta_spend,
                          SUM(spend) FILTER (channel = 'google') google_spend
                   FROM spend WHERE spend_date BETWEEN ? AND ? GROUP BY 1
               )
               SELECT days.day, l.* EXCLUDE (day), s.* EXCLUDE (day)
               FROM days LEFT JOIN l USING (day) LEFT JOIN s USING (day)
               ORDER BY days.day""",
            [start, end, start, end, start, end],
        )
        money = ["gross", "discounts", "refunds_rto", "cogs", "shipping", "fees",
                 "ad_spend", "meta_spend", "google_spend"]
        df[money] = df[money].astype(float).fillna(0.0)
        df["orders"] = df["orders"].fillna(0).astype(int)
        df["net_revenue"] = fin.net_revenue(df["gross"], df["discounts"], df["refunds_rto"])
        df["cm1"] = fin.cm1(df["net_revenue"], df["cogs"])
        df["cm2"] = fin.cm2(df["cm1"], df["shipping"], df["fees"], df["ad_spend"])
        out = []
        for row in df.to_dict("records"):
            rec = {"date": row["day"].date().isoformat() if hasattr(row["day"], "date") else str(row["day"])}
            for key in money + ["net_revenue", "cm1", "cm2"]:
                rec[key] = _r(row[key])
            rec["orders"] = row["orders"]
            rec["mer"] = _r(fin.blended_mer(row["net_revenue"], row["ad_spend"]), 4)
            out.append(rec)
        return out

    # ------------------------------------------------------------------ channels

    def channels(self, start: date, end: date) -> list[dict]:
        df = self._df(
            """WITH s AS (
                   SELECT channel, SUM(spend) spend, SUM(platform_reported_revenue) platform_revenue,
                          SUM(impressions) impressions, SUM(clicks) clicks
                   FROM spend WHERE spend_date BETWEEN ? AND ? GROUP BY 1
               ), o AS (
                   SELECT channel, SUM(gross) gross, SUM(discount) discounts, SUM(returns) refunds_rto,
                          SUM(cogs) cogs, COUNT(*) orders
                   FROM fact_orders WHERE order_date BETWEEN ? AND ? GROUP BY 1
               ), c AS (
                   SELECT acquisition_channel AS channel, COUNT(*) new_customers
                   FROM dim_customers WHERE first_order_date BETWEEN ? AND ? GROUP BY 1
               )
               SELECT COALESCE(s.channel, o.channel, c.channel) AS channel, s.* EXCLUDE (channel),
                      o.* EXCLUDE (channel), c.* EXCLUDE (channel)
               FROM s FULL OUTER JOIN o ON s.channel = o.channel
                      FULL OUTER JOIN c ON COALESCE(s.channel, o.channel) = c.channel
               ORDER BY spend DESC NULLS LAST, channel""",
            [start, end, start, end, start, end],
        )
        out = []
        for row in df.to_dict("records"):
            spend, platform_rev = _num(row["spend"]), _num(row["platform_revenue"])
            gross, disc, ret = _num(row["gross"]), _num(row["discounts"]), _num(row["refunds_rto"])
            net = fin.net_revenue(gross, disc, ret)
            new_cust = int(_num(row["new_customers"]))
            true_roas = fin.true_roas(net, spend)
            plat_roas = fin.platform_roas(platform_rev, spend)
            out.append({
                "channel": row["channel"],
                "ad_spend": _r(spend),
                "impressions": int(_num(row["impressions"])),
                "clicks": int(_num(row["clicks"])),
                "platform_reported_revenue": _r(platform_rev),
                "platform_roas": _r(plat_roas, 4),
                "gross_revenue": _r(gross),
                "refunds_rto": _r(ret),
                "attributed_net_revenue": _r(net),
                "true_roas": _r(true_roas, 4),
                "roas_inflation": _r(fin.safe_div(plat_roas, true_roas) if plat_roas and true_roas else None, 4),
                "orders": int(_num(row["orders"])),
                "new_customers": new_cust,
                "cac": _r(fin.blended_cac(spend, new_cust)) if spend > 0 else None,
            })
        return out

    # ------------------------------------------------------------------ payment mix

    def payment_mix(self, start: date, end: date) -> list[dict]:
        """COD vs prepaid: volume, RTO incidence and the cost RTO leaves behind."""
        df = self._df(
            """SELECT payment_method, COUNT(*) orders, COUNT(*) FILTER (status = 'rto') rto_orders,
                      SUM(gross - discount - returns) net_revenue,
                      SUM(returns) FILTER (status = 'rto') rto_reversed_revenue,
                      SUM(shipping) FILTER (status = 'rto') rto_shipping,
                      SUM(fees) FILTER (status = 'rto') rto_fees
               FROM fact_orders WHERE order_date BETWEEN ? AND ?
               GROUP BY 1 ORDER BY 1""",
            [start, end],
        )
        total = int(df["orders"].sum()) if len(df) else 0
        out = []
        for r in df.to_dict("records"):
            orders, rto = int(r["orders"]), int(r["rto_orders"])
            out.append({
                "payment_method": r["payment_method"],
                "orders": orders,
                "order_share": _r(fin.safe_div(orders, total), 4),
                "rto_orders": rto,
                "rto_rate": _r(fin.safe_div(rto, orders), 4),
                "net_revenue": _r(_num(r["net_revenue"])),
                "rto_reversed_revenue": _r(_num(r["rto_reversed_revenue"])),
                "rto_logistics_cost": _r(_num(r["rto_shipping"]) + _num(r["rto_fees"])),
            })
        return out

    # ------------------------------------------------------------------ cohorts

    def cohorts(self) -> dict:
        as_of = self.as_of()
        as_of_month = date(as_of.year, as_of.month, 1)
        sizes = self._df(
            """SELECT c.cohort_month, COUNT(*) AS size,
                      (SELECT SUM(spend) FROM spend
                        WHERE CAST(date_trunc('month', spend_date) AS DATE) = c.cohort_month) AS spend
               FROM dim_customers c GROUP BY c.cohort_month ORDER BY c.cohort_month"""
        )
        activity = self._df(
            """SELECT c.cohort_month,
                      date_diff('month', c.cohort_month, CAST(date_trunc('month', o.order_date) AS DATE)) AS m,
                      COUNT(DISTINCT o.customer_ref) FILTER (o.status <> 'rto') AS active,
                      SUM(o.gross - o.discount - o.returns) AS net_revenue,
                      SUM(o.gross - o.discount - o.returns - o.cogs - o.shipping - o.fees) AS contribution
               FROM fact_orders o JOIN dim_customers c USING (customer_ref)
               GROUP BY 1, 2"""
        )
        act = {
            (pd.Timestamp(r["cohort_month"]).date(), int(r["m"])): r
            for r in activity.to_dict("records")
        }
        rows = []
        for rec in sizes.to_dict("records"):
            month = pd.Timestamp(rec["cohort_month"]).date()
            size = int(rec["size"])
            spend = _num(rec["spend"])
            cac = fin.blended_cac(spend, size)
            cum_net = cum_contrib = 0.0
            cells, payback = [], None
            for m in range(COHORT_MAX_OFFSET + 1):
                cal_month = _month_add(month, m)
                if cal_month > as_of_month:
                    cells.append({"month": m, "active": None, "retention": None,
                                  "ltv_net_revenue": None, "ltv_contribution": None,
                                  "partial": False, "available": False})
                    continue
                a = act.get((month, m))
                net = _num(a["net_revenue"]) if a is not None else 0.0
                contrib = _num(a["contribution"]) if a is not None else 0.0
                cum_net += net
                cum_contrib += contrib
                # M0 is the acquisition month: every cohort member is active by definition.
                active = size if m == 0 else (int(a["active"]) if a is not None else 0)
                ltv_net = fin.safe_div(cum_net, size)
                ltv_contrib = fin.safe_div(cum_contrib, size)
                if payback is None and cac is not None and ltv_contrib is not None and ltv_contrib >= cac:
                    payback = m
                cells.append({
                    "month": m,
                    "active": active,
                    "retention": _r(fin.cohort_retention_rate(active, size), 4),
                    "ltv_net_revenue": _r(ltv_net),
                    "ltv_contribution": _r(ltv_contrib),
                    "partial": cal_month == as_of_month,
                    "available": True,
                })
            last = next((c for c in reversed(cells) if c["available"]), None)
            rows.append({
                "cohort_month": month.isoformat(),
                "size": size,
                "ad_spend": _r(spend),
                "cac": _r(cac),
                "cells": cells,
                "payback_month": payback,
                "ltv_to_cac": _r(fin.safe_div(last["ltv_contribution"], cac)
                                 if last and cac and last["ltv_contribution"] is not None else None, 4),
            })
        return {"as_of": as_of.isoformat(), "max_offset": COHORT_MAX_OFFSET, "cohorts": rows}

    # ------------------------------------------------------------------ SKUs

    def skus(self, start: date, end: date) -> dict:
        df = self._df(
            """WITH l AS (
                   SELECT sku, SUM(quantity) units, COUNT(DISTINCT order_id) orders,
                          SUM(gross) gross, SUM(discount) discounts, SUM(returns) refunds_rto,
                          SUM(cogs) cogs, SUM(shipping) shipping, SUM(fees) fees,
                          COUNT(DISTINCT order_id) FILTER (status IN ('rto', 'refunded')) returned_orders
                   FROM fact_lines WHERE order_date BETWEEN ? AND ? GROUP BY 1
               ), rep AS (
                   -- repeat rate: share of the SKU's buyers (to date) who bought it in 2+ orders
                   SELECT sku, COUNT(*) buyers, COUNT(*) FILTER (n >= 2) repeat_buyers FROM (
                       SELECT sku, customer_ref, COUNT(DISTINCT order_id) n
                       FROM fact_lines WHERE status <> 'rto' AND order_date <= ?
                       GROUP BY 1, 2) GROUP BY 1
               ), direct AS (
                   SELECT s.sku, SUM(s.spend) direct_spend FROM spend s
                   WHERE s.spend_date BETWEEN ? AND ? AND s.sku IN (SELECT sku FROM l)
                   GROUP BY 1
               )
               SELECT l.*, COALESCE(rep.buyers, 0) buyers, COALESCE(rep.repeat_buyers, 0) repeat_buyers,
                      COALESCE(direct.direct_spend, 0) direct_spend, p.name, p.category
               FROM l LEFT JOIN rep USING (sku) LEFT JOIN direct USING (sku)
                      LEFT JOIN products p USING (sku)""",
            [start, end, end, start, end],
        )
        (pooled,) = self._one(
            """SELECT SUM(spend) FROM spend WHERE spend_date BETWEEN ? AND ?
               AND (sku IS NULL OR sku NOT IN (
                   SELECT DISTINCT sku FROM fact_lines WHERE order_date BETWEEN ? AND ?))""",
            [start, end, start, end],
        )
        pooled = _num(pooled)
        money = ["gross", "discounts", "refunds_rto", "cogs", "shipping", "fees", "direct_spend"]
        df[money] = df[money].astype(float).fillna(0.0)
        df["net_revenue"] = fin.net_revenue(df["gross"], df["discounts"], df["refunds_rto"])
        # Unmapped (brand / catalogue) spend is spread by positive net revenue share.
        weights = df["net_revenue"].clip(lower=0)
        if weights.sum() <= 0:
            weights = pd.Series(1.0, index=df.index)
        df["ad_spend"] = df["direct_spend"] + (pooled * weights / weights.sum() if len(df) else 0)
        df["cm1"] = fin.cm1(df["net_revenue"], df["cogs"])
        df["cm2"] = fin.cm2(df["cm1"], df["shipping"], df["fees"], df["ad_spend"])

        rows = []
        for r in df.to_dict("records"):
            net = r["net_revenue"]
            cm2_pct = fin.safe_div(r["cm2"], net)
            sold = r["gross"] - r["discounts"]
            return_rate = fin.safe_div(r["refunds_rto"], sold)
            repeat_rate = fin.safe_div(int(r["repeat_buyers"]), int(r["buyers"]))
            ad_to_rev = fin.safe_div(r["ad_spend"], net) if net > 0 else None
            reasons = []
            if r["cm2"] < 0:
                reasons.append("negative CM2")
            if (return_rate or 0) >= LEAK_MIN_RETURN_RATE and (
                ad_to_rev is None or ad_to_rev >= LEAK_MIN_AD_TO_REVENUE
            ):
                reasons.append("high refund/RTO on heavy ad spend")
            if reasons:
                segment = "margin_leak"
            elif (repeat_rate or 0) >= HERO_MIN_REPEAT_RATE and (cm2_pct or 0) >= HERO_MIN_CM2_PCT:
                segment = "hero"
                reasons.append("high repeat rate with healthy CM2")
            else:
                segment = "core"
            rows.append({
                "sku": r["sku"],
                "name": r["name"] if isinstance(r["name"], str) else r["sku"],
                "category": r["category"] if isinstance(r["category"], str) else None,
                "units": int(r["units"]),
                "orders": int(r["orders"]),
                "gross_revenue": _r(r["gross"]),
                "discounts": _r(r["discounts"]),
                "refunds_rto": _r(r["refunds_rto"]),
                "net_revenue": _r(net),
                "cogs": _r(r["cogs"]),
                "cm1": _r(r["cm1"]),
                "shipping_fees": _r(r["shipping"] + r["fees"]),
                "ad_spend": _r(r["ad_spend"]),
                "cm2": _r(r["cm2"]),
                "cm2_pct": _r(cm2_pct, 4),
                "return_rate": _r(return_rate, 4),
                "repeat_rate": _r(repeat_rate, 4),
                "ad_to_revenue": _r(ad_to_rev, 4),
                "true_roas": _r(fin.true_roas(net, r["ad_spend"]), 4),
                "segment": segment,
                "reasons": reasons,
            })
        rows.sort(key=lambda x: x["cm2"] or 0, reverse=True)
        return {
            "thresholds": {
                "hero_min_repeat_rate": HERO_MIN_REPEAT_RATE,
                "hero_min_cm2_pct": HERO_MIN_CM2_PCT,
                "leak_min_return_rate": LEAK_MIN_RETURN_RATE,
                "leak_min_ad_to_revenue": LEAK_MIN_AD_TO_REVENUE,
            },
            "skus": rows,
        }
