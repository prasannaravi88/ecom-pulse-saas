"""CSV ingestion for orders (line-level, Shopify-export compatible) and daily ad spend.

Uploads are all-or-nothing: the file is fully validated first and, if any row
fails, nothing is written and every error is returned with its CSV line number.
Re-uploading an order_ref replaces that order (idempotent); ad-spend rows upsert
on (date, channel, campaign). All writes are scoped to the active tenant.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import date

import pandas as pd
from sqlalchemy import delete, insert, select, tuple_
from sqlalchemy.orm import Session

from app.db.models import DailyAdSpend, Order, OrderItem, Product

MAX_ERRORS = 50

ORDER_REQUIRED = ["order_id", "order_date", "customer_id", "sku", "quantity", "unit_price"]
ORDER_OPTIONAL = {
    "discount": 0.0, "refund_amount": 0.0, "unit_cogs": None, "status": "delivered",
    "payment_method": "prepaid", "channel": "organic", "shipping_cost": 0.0, "payment_fee": 0.0,
    "product_name": None,
}
SPEND_REQUIRED = ["date", "channel", "campaign", "spend"]
SPEND_OPTIONAL = {"sku": None, "impressions": 0, "clicks": 0, "platform_reported_revenue": 0.0}

# Shopify "Export orders" headers -> canonical names.
ORDER_ALIASES = {
    "name": "order_id", "order": "order_id", "order_number": "order_id",
    "created_at": "order_date", "date": "order_date",
    "email": "customer_id", "customer_email": "customer_id", "customer": "customer_id",
    "lineitem_sku": "sku", "lineitem_quantity": "quantity", "lineitem_price": "unit_price",
    "lineitem_discount": "discount", "lineitem_name": "product_name",
    "refunded_amount": "refund_amount", "refund": "refund_amount",
    "shipping": "shipping_cost", "payment_gateway_fee": "payment_fee", "cogs": "unit_cogs",
    "attributed_channel": "channel", "utm_source": "channel", "financial_status": "status",
}
SPEND_ALIASES = {
    "day": "date", "spend_date": "date", "platform": "channel", "source": "channel",
    "campaign_name": "campaign", "amount_spent": "spend", "cost": "spend", "amount": "spend",
    "conversion_value": "platform_reported_revenue", "purchases_conversion_value": "platform_reported_revenue",
    "conv_value": "platform_reported_revenue", "link_clicks": "clicks",
}
STATUS_ALIASES = {
    "delivered": "delivered", "paid": "delivered", "fulfilled": "delivered", "pending": "delivered",
    "rto": "rto", "returned_to_origin": "rto", "refunded": "refunded", "partially_refunded": "refunded",
    "cancelled": "cancelled", "canceled": "cancelled", "voided": "cancelled",
}
PAYMENT_ALIASES = {"cod": "cod", "cash_on_delivery": "cod", "cash_on_delivery_(cod)": "cod"}
CHANNEL_ALIASES = {"facebook": "meta", "instagram": "meta", "fb": "meta", "ig": "meta",
                   "adwords": "google", "google_ads": "google"}


class IngestionError(Exception):
    def __init__(self, errors: list[dict], columns: list[str] | None = None):
        super().__init__(f"{len(errors)} validation error(s)")
        self.errors = errors
        self.columns = columns


@dataclass
class IngestResult:
    rows: int
    inserted: int = 0
    replaced: int = 0
    products_created: list[str] = field(default_factory=list)


def _norm(name: str) -> str:
    return "_".join(str(name).strip().lower().replace("-", " ").replace("/", " ").split())


def _read(content: bytes, aliases: dict[str, str], required: list[str], optional: dict) -> pd.DataFrame:
    try:
        df = pd.read_csv(io.BytesIO(content), dtype=str, keep_default_na=False, skipinitialspace=True)
    except Exception as exc:  # malformed CSV, wrong encoding, empty file
        raise IngestionError([{"row": None, "column": None, "message": f"Could not parse CSV: {exc}"}])
    renamed = {}
    for col in df.columns:
        key = _norm(col)
        key = aliases.get(key, key)
        if key not in renamed.values():
            renamed[col] = key
    df = df[list(renamed)].rename(columns=renamed)
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise IngestionError(
            [{"row": None, "column": c, "message": "required column missing"} for c in missing],
            columns=list(df.columns),
        )
    if df.empty:
        raise IngestionError([{"row": None, "column": None, "message": "file has no data rows"}])
    for col, default in optional.items():
        if col not in df.columns:
            df[col] = "" if default is None else str(default)
    return df.apply(lambda s: s.str.strip())


class _Errors:
    def __init__(self):
        self.items: list[dict] = []

    def add(self, line: int, column: str, message: str) -> None:
        if len(self.items) < MAX_ERRORS:
            self.items.append({"row": line, "column": column, "message": message})

    def __bool__(self):
        return bool(self.items)


def _money(raw: str, line: int, column: str, errors: _Errors, default: float | None = 0.0,
           allow_negative: bool = False) -> float | None:
    if raw == "":
        return default
    try:
        value = float(raw.replace(",", "").lstrip("₹$"))
    except ValueError:
        errors.add(line, column, f"not a number: {raw!r}")
        return None
    if value != value or value in (float("inf"), float("-inf")):
        errors.add(line, column, f"not a finite number: {raw!r}")
        return None
    if value < 0 and not allow_negative:
        errors.add(line, column, f"must be >= 0, got {raw}")
        return None
    return round(value, 2)


def _date(raw: str, line: int, column: str, errors: _Errors) -> date | None:
    try:
        ts = pd.Timestamp(raw)
    except (ValueError, TypeError):
        ts = None
    if ts is None or pd.isna(ts):
        errors.add(line, column, f"invalid date: {raw!r}")
        return None
    return ts.date()


def _int(raw: str, line: int, column: str, errors: _Errors, minimum: int = 0, default: int = 0) -> int | None:
    if raw == "":
        return default
    try:
        value = float(raw.replace(",", ""))
    except ValueError:
        errors.add(line, column, f"not an integer: {raw!r}")
        return None
    if value != int(value) or value < minimum:
        errors.add(line, column, f"must be a whole number >= {minimum}, got {raw}")
        return None
    return int(value)


def ingest_orders(session: Session, tenant_id: str, content: bytes) -> IngestResult:
    df = _read(content, ORDER_ALIASES, ORDER_REQUIRED, ORDER_OPTIONAL)
    errors = _Errors()
    catalog = {
        sku: float(cogs)
        for sku, cogs in session.execute(
            select(Product.sku, Product.unit_cogs).where(Product.tenant_id == tenant_id)
        ).all()
    }
    orders: dict[str, dict] = {}
    new_products: dict[str, dict] = {}

    for pos, row in enumerate(df.to_dict("records")):
        line = pos + 2  # 1-based, after header
        ref = row["order_id"]
        if not ref:
            errors.add(line, "order_id", "required")
        customer = row["customer_id"] or None
        if not customer:
            errors.add(line, "customer_id", "required")
        sku = row["sku"]
        if not sku:
            errors.add(line, "sku", "required")
        order_date = _date(row["order_date"], line, "order_date", errors)
        qty = _int(row["quantity"], line, "quantity", errors, minimum=1, default=None)
        if row["quantity"] == "":
            errors.add(line, "quantity", "required")
        price = _money(row["unit_price"], line, "unit_price", errors, default=None)
        if row["unit_price"] == "":
            errors.add(line, "unit_price", "required")
        discount = _money(row["discount"], line, "discount", errors)
        refund = _money(row["refund_amount"], line, "refund_amount", errors)
        shipping = _money(row["shipping_cost"], line, "shipping_cost", errors)
        fee = _money(row["payment_fee"], line, "payment_fee", errors)
        cogs = _money(row["unit_cogs"], line, "unit_cogs", errors, default=None)

        status = STATUS_ALIASES.get(_norm(row["status"]) or "delivered")
        if status is None:
            errors.add(line, "status", f"unknown status {row['status']!r} "
                       "(use delivered, rto, refunded or cancelled)")
        pay_key = _norm(row["payment_method"])
        payment = PAYMENT_ALIASES.get(pay_key, "prepaid")
        channel = _norm(row["channel"]) or "organic"
        channel = CHANNEL_ALIASES.get(channel, channel)

        if sku and cogs is None and sku not in catalog and sku not in new_products:
            errors.add(line, "unit_cogs", f"unknown SKU {sku!r}: provide unit_cogs to create it")
        if None in (order_date, qty, price, discount, refund):
            continue
        line_gross = qty * price
        if discount > line_gross:
            errors.add(line, "discount", f"discount {discount} exceeds line value {line_gross:.2f}")
            continue
        if refund > line_gross - discount + 0.005:
            errors.add(line, "refund_amount", f"refund {refund} exceeds amount paid {line_gross - discount:.2f}")
            continue
        if not (ref and customer and sku and status):
            continue
        if sku not in catalog and sku not in new_products and cogs is not None:
            new_products[sku] = {"sku": sku, "name": row["product_name"] or sku,
                                 "category": "Uncategorised", "price": price, "unit_cogs": cogs}

        order = orders.get(ref)
        header = (order_date, customer, status, payment, channel)
        if order is None:
            order = orders[ref] = {
                "order_ref": ref, "order_date": order_date, "customer_ref": customer,
                "status": status, "payment_method": payment, "attributed_channel": channel,
                "shipping_cost": shipping or 0.0, "payment_fee": fee or 0.0, "items": [],
                "_header": header, "_line": line,
            }
        elif order["_header"] != header:
            errors.add(line, "order_id", f"order {ref} has conflicting date/customer/status/"
                       f"payment/channel with line {order['_line']}")
            continue
        else:
            # Shopify repeats order-level values on every line; take the first non-zero.
            order["shipping_cost"] = order["shipping_cost"] or (shipping or 0.0)
            order["payment_fee"] = order["payment_fee"] or (fee or 0.0)
        unit_cogs = cogs if cogs is not None else catalog.get(sku, new_products.get(sku, {}).get("unit_cogs"))
        if status == "rto":
            refund = 0.0  # RTO lines are reversed in full by the analytics engine
        order["items"].append({"sku": sku, "quantity": qty, "unit_price": price,
                               "discount": discount, "refund_amount": refund, "unit_cogs": unit_cogs})

    if errors:
        raise IngestionError(errors.items, columns=list(df.columns))

    result = IngestResult(rows=len(df), products_created=sorted(new_products))
    with session.begin_nested():
        if new_products:
            session.execute(insert(Product), [{"tenant_id": tenant_id, **p} for p in new_products.values()])
        refs = list(orders)
        existing = []
        for i in range(0, len(refs), 1000):
            existing += session.scalars(
                select(Order.id).where(Order.tenant_id == tenant_id, Order.order_ref.in_(refs[i:i + 1000]))
            ).all()
        if existing:
            for i in range(0, len(existing), 1000):
                chunk = existing[i:i + 1000]
                session.execute(delete(OrderItem).where(OrderItem.tenant_id == tenant_id,
                                                        OrderItem.order_id.in_(chunk)))
                session.execute(delete(Order).where(Order.tenant_id == tenant_id, Order.id.in_(chunk)))
        result.replaced = len(existing)
        result.inserted = len(orders) - len(existing)
        header_rows = [{k: v for k, v in o.items() if k != "items" and not k.startswith("_")}
                       | {"tenant_id": tenant_id} for o in orders.values()]
        ids = {ref: oid for oid, ref in session.execute(
            insert(Order).returning(Order.id, Order.order_ref), header_rows).all()}
        session.execute(insert(OrderItem), [
            {"tenant_id": tenant_id, "order_id": ids[o["order_ref"]], **it}
            for o in orders.values() for it in o["items"]
        ])
    session.commit()
    return result


def ingest_ad_spend(session: Session, tenant_id: str, content: bytes) -> IngestResult:
    df = _read(content, SPEND_ALIASES, SPEND_REQUIRED, SPEND_OPTIONAL)
    errors = _Errors()
    rows: dict[tuple, dict] = {}
    for pos, row in enumerate(df.to_dict("records")):
        line = pos + 2
        day = _date(row["date"], line, "date", errors)
        channel = _norm(row["channel"])
        channel = CHANNEL_ALIASES.get(channel, channel)
        if not channel:
            errors.add(line, "channel", "required")
        campaign = row["campaign"]
        if not campaign:
            errors.add(line, "campaign", "required")
        if row["spend"] == "":
            errors.add(line, "spend", "required")
        spend = _money(row["spend"], line, "spend", errors, default=None)
        impressions = _int(row["impressions"], line, "impressions", errors)
        clicks = _int(row["clicks"], line, "clicks", errors)
        claimed = _money(row["platform_reported_revenue"], line, "platform_reported_revenue", errors)
        if None in (day, spend, impressions, clicks, claimed) or not (channel and campaign):
            continue
        key = (day, channel, campaign)
        if key in rows:
            errors.add(line, "campaign", f"duplicate row for {day} / {channel} / {campaign}")
            continue
        rows[key] = {"spend_date": day, "channel": channel, "campaign": campaign,
                     "sku": row["sku"] or None, "spend": spend, "impressions": impressions,
                     "clicks": clicks, "platform_reported_revenue": claimed}
    if errors:
        raise IngestionError(errors.items, columns=list(df.columns))

    result = IngestResult(rows=len(df))
    keys = list(rows)
    with session.begin_nested():
        replaced = 0
        for i in range(0, len(keys), 500):
            chunk = keys[i:i + 500]
            replaced += session.execute(
                delete(DailyAdSpend).where(
                    DailyAdSpend.tenant_id == tenant_id,
                    tuple_(DailyAdSpend.spend_date, DailyAdSpend.channel, DailyAdSpend.campaign).in_(chunk),
                )
            ).rowcount
        session.execute(insert(DailyAdSpend), [{"tenant_id": tenant_id, **r} for r in rows.values()])
    session.commit()
    result.replaced = replaced
    result.inserted = len(rows) - replaced
    return result


ORDERS_TEMPLATE = (
    "order_id,order_date,customer_id,sku,quantity,unit_price,discount,refund_amount,unit_cogs,"
    "status,payment_method,channel,shipping_cost,payment_fee\n"
    "WEB-5001,2026-11-18,cust_0001,SUN-SPF50,2,649,130,0,,delivered,prepaid,meta,74,23.36\n"
    "WEB-5001,2026-11-18,cust_0001,CLN-GEL-100,1,349,35,0,,delivered,prepaid,meta,74,23.36\n"
    "WEB-5002,2026-11-18,cust_0002,DEV-LED-COMB,1,2499,0,0,,rto,cod,meta,124,0\n"
)
SPEND_TEMPLATE = (
    "date,channel,campaign,spend,sku,impressions,clicks,platform_reported_revenue\n"
    "2026-11-18,meta,Meta | Prospecting | SUN-SPF50,4200,SUN-SPF50,21000,260,15800\n"
    "2026-11-18,google,Google | Search | Brand,1850,,6100,410,9200\n"
)
