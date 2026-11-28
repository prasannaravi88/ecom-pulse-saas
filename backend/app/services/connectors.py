"""Local-only data connectors.

Each provider (Shopify, Meta Ads, Google Ads, webhook) has a *Local Simulator* that generates a
batch of plausible activity for the last two days and writes it through the same validated CSV
ingestion path as an upload, so every metric recomputes exactly as it would for real data.
Live provider APIs are deliberately not implemented: they need approved apps and credentials.

Each sync is a new batch. Orders get fresh refs (`SIM-<prov>-<seq>-<date>-<n>`) so they accumulate;
simulator ad-spend rows keep one fixed campaign per day and add each batch to the running total.
Generation is seeded from (tenant, provider, sync number), so a given sync is reproducible.
"""

from __future__ import annotations

import csv
import io
import random
import zlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Connection, DailyAdSpend, Order, Product, Tenant
from app.services import ingestion

PROVIDERS = {
    "shopify": {"label": "Shopify", "kind": "orders", "prefix": "SHP"},
    "meta": {"label": "Meta Ads", "kind": "ad-spend", "channel": "meta"},
    "google": {"label": "Google Ads", "kind": "ad-spend", "channel": "google"},
    "webhook": {"label": "Webhook API", "kind": "orders", "prefix": "WH"},
}
MODES = ("simulator",)
BATCH_DAYS = 2
MAX_WEBHOOK_ORDERS = 500

_COSTS = {  # (shipping per order, fixed gateway fee) in the tenant's own currency
    "INR": (74.0, 3.0),
    "USD": (6.5, 0.30),
}
_SPEND_FALLBACK = {"INR": 9000.0, "USD": 450.0}
_CAMPAIGNS = {
    "meta": "Meta | Simulator | Prospecting",
    "google": "Google | Simulator | Search",
}


class ConnectorError(Exception):
    pass


@dataclass
class SyncResult:
    provider: str
    sync_number: int
    orders_inserted: int = 0
    orders_replaced: int = 0
    spend_rows: int = 0
    summary: str = ""


def _rng(tenant_id: str, provider: str, seq: int) -> random.Random:
    return random.Random(zlib.crc32(f"{tenant_id}|{provider}|{seq}".encode()))


def _window(session: Session, tenant_id: str) -> list[date]:
    """The last BATCH_DAYS days ending at the tenant's latest data date (today if it has none).

    Anchoring to the data, not the clock, keeps a long-lived local database free of gaps: the
    dashboard's "as of" date never jumps ahead of the data it already has.
    """
    latest = [d for d in (
        session.scalar(select(func.max(Order.order_date)).where(Order.tenant_id == tenant_id)),
        session.scalar(select(func.max(DailyAdSpend.spend_date)).where(DailyAdSpend.tenant_id == tenant_id)),
    ) if d is not None]
    end = min(max(latest), date.today()) if latest else date.today()
    return [end - timedelta(days=i) for i in range(BATCH_DAYS - 1, -1, -1)]


def _aware(dt: datetime) -> datetime:
    """SQLite returns naive datetimes; everything we store is UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _get(session: Session, tenant_id: str, provider: str) -> Connection | None:
    return session.scalar(select(Connection).where(Connection.tenant_id == tenant_id,
                                                    Connection.provider == provider))


def list_connections(session: Session, tenant_id: str) -> list[dict]:
    rows = {c.provider: c for c in session.scalars(select(Connection).where(Connection.tenant_id == tenant_id))}
    out = []
    for key, meta in PROVIDERS.items():
        c = rows.get(key)
        out.append({
            "provider": key, "label": meta["label"], "kind": meta["kind"],
            "modes": list(MODES), "mode": c.mode if c else "simulator",
            "connected": c is not None and c.sync_count > 0,
            "last_synced_at": _aware(c.last_synced_at).isoformat() if c and c.last_synced_at else None,
            "sync_count": c.sync_count if c else 0,
            "orders_total": c.orders_total if c else 0,
            "spend_rows_total": c.spend_rows_total if c else 0,
            "last_summary": c.last_summary if c else None,
        })
    return out


def _recent_baseline(session: Session, tenant_id: str, channel: str | None) -> tuple[float, float]:
    """(avg orders/day, avg daily spend for `channel`) over the tenant's latest 30 days of data."""
    last = session.scalar(select(func.max(Order.order_date)).where(Order.tenant_id == tenant_id))
    if last is None:
        return 0.0, 0.0
    since = last - timedelta(days=29)
    orders = session.scalar(select(func.count()).select_from(Order).where(
        Order.tenant_id == tenant_id, Order.order_date >= since, Order.order_date <= last)) or 0
    spend = 0.0
    if channel:
        spend = float(session.scalar(select(func.coalesce(func.sum(DailyAdSpend.spend), 0)).where(
            DailyAdSpend.tenant_id == tenant_id, DailyAdSpend.channel == channel,
            DailyAdSpend.spend_date >= since, DailyAdSpend.spend_date <= last)) or 0)
    return orders / 30, spend / 30


def _csv(header: list[str], rows: list[list]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue().encode()


ORDER_HEADER = ["order_id", "order_date", "customer_id", "sku", "quantity", "unit_price", "discount",
                "refund_amount", "unit_cogs", "status", "payment_method", "channel", "shipping_cost",
                "payment_fee"]


def _simulate_orders(session: Session, tenant: Tenant, provider: str, seq: int, count_scale: float) -> bytes:
    rng = _rng(tenant.id, provider, seq)
    products = session.execute(select(Product.sku, Product.price, Product.unit_cogs)
                               .where(Product.tenant_id == tenant.id).order_by(Product.sku)).all()
    if not products:
        raise ConnectorError("This tenant has no products yet. Upload an orders CSV with unit_cogs first.")
    repeat_pool = session.scalars(select(Order.customer_ref).where(Order.tenant_id == tenant.id)
                                  .order_by(Order.id.desc()).limit(400)).all()
    avg_orders, _ = _recent_baseline(session, tenant.id, None)
    per_day = max(6, round((avg_orders or 40) * 0.18 * count_scale))
    ship, fixed_fee = _COSTS[tenant.currency]
    prefix = PROVIDERS[provider]["prefix"]
    rows: list[list] = []
    for day in _window(session, tenant.id):
        for n in range(per_day):
            ref = f"SIM-{prefix}-{seq:04d}-{day:%m%d}-{n + 1:03d}"
            customer = (rng.choice(repeat_pool) if repeat_pool and rng.random() < 0.3
                        else f"sim_{seq:04d}_{day:%m%d}_{n + 1:03d}")
            payment = "cod" if rng.random() < 0.38 else "prepaid"
            channel = rng.choices(["meta", "google", "organic"], [0.4, 0.25, 0.35])[0]
            roll = rng.random()
            rto_p = 0.14 if payment == "cod" else 0.02
            status = ("rto" if roll < rto_p else "refunded" if roll < rto_p + 0.03
                      else "cancelled" if roll < rto_p + 0.05 else "delivered")
            picked = []
            for sku, price, cogs in rng.sample(products, k=rng.choices([1, 2], [0.8, 0.2])[0]):
                qty = 2 if rng.random() < 0.08 else 1
                price, cogs = float(price), float(cogs)
                discount = round(qty * price * rng.choice([0, 0, 0.05, 0.1, 0.2]), 2)
                picked.append((sku, qty, price, cogs, discount))
            paid = sum(q * p - d for _, q, p, _, d in picked)
            fee = 0.0 if payment == "cod" else round(0.02 * paid + fixed_fee, 2)
            for i, (sku, qty, price, cogs, discount) in enumerate(picked):
                refund = round(qty * price - discount, 2) if status == "refunded" else 0.0
                rows.append([ref, day.isoformat(), customer, sku, qty, price, discount, refund, cogs,
                             status, payment, channel, ship if i == 0 else 0, fee if i == 0 else 0])
    return _csv(ORDER_HEADER, rows)


def _simulate_spend(session: Session, tenant: Tenant, provider: str, seq: int) -> bytes:
    channel = PROVIDERS[provider]["channel"]
    campaign = _CAMPAIGNS[channel]
    rng = _rng(tenant.id, provider, seq)
    _, avg_spend = _recent_baseline(session, tenant.id, channel)
    batch_spend = (avg_spend or _SPEND_FALLBACK[tenant.currency]) * 0.2
    roas_claim = {"meta": 3.4, "google": 2.9}[channel]
    cpm = 160 if tenant.currency == "INR" else 11
    rows = []
    for day in _window(session, tenant.id):
        existing = session.scalar(select(DailyAdSpend).where(
            DailyAdSpend.tenant_id == tenant.id, DailyAdSpend.spend_date == day,
            DailyAdSpend.channel == channel, DailyAdSpend.campaign == campaign))
        add = round(batch_spend * rng.uniform(0.8, 1.25), 2)
        impressions = int(add / cpm * 1000)
        clicks = int(impressions * rng.uniform(0.012, 0.022))
        claimed = round(add * roas_claim * rng.uniform(0.85, 1.15), 2)
        if existing:  # running total for the day, not a replacement
            add += float(existing.spend)
            impressions += existing.impressions
            clicks += existing.clicks
            claimed += float(existing.platform_reported_revenue)
        rows.append([day.isoformat(), channel, campaign, round(add, 2), "", impressions, clicks, round(claimed, 2)])
    return _csv(["date", "channel", "campaign", "spend", "sku", "impressions", "clicks",
                 "platform_reported_revenue"], rows)


def sync(session: Session, tenant: Tenant, provider: str, mode: str = "simulator") -> SyncResult:
    """Run one sync for `tenant`. Raises ConnectorError (400-class) or ingestion.IngestionError."""
    if provider not in PROVIDERS:
        raise ConnectorError(f"Unknown provider {provider!r}")
    if mode != "simulator":
        raise ConnectorError("Only the Local Simulator is available. Live APIs need approved app credentials.")
    conn = _get(session, tenant.id, provider)
    seq = (conn.sync_count if conn else 0) + 1
    result = SyncResult(provider=provider, sync_number=seq)
    label = PROVIDERS[provider]["label"]

    if PROVIDERS[provider]["kind"] == "orders":
        scale = 0.4 if provider == "webhook" else 1.0
        res = ingestion.ingest_orders(session, tenant.id, _simulate_orders(session, tenant, provider, seq, scale))
        result.orders_inserted, result.orders_replaced = res.inserted, res.replaced
        result.summary = f"{label}: {res.inserted} sample orders added"
    else:
        res = ingestion.ingest_ad_spend(session, tenant.id, _simulate_spend(session, tenant, provider, seq))
        result.spend_rows = res.inserted + res.replaced
        result.summary = f"{label}: spend updated for {BATCH_DAYS} days ({result.spend_rows} rows)"

    record(session, tenant.id, provider, mode, result)
    return result


def record(session: Session, tenant_id: str, provider: str, mode: str | None, result: SyncResult) -> None:
    conn = _get(session, tenant_id, provider)
    if conn is None:
        conn = Connection(tenant_id=tenant_id, provider=provider, sync_count=0, orders_total=0, spend_rows_total=0)
        session.add(conn)
    if mode:
        conn.mode = mode
    conn.sync_count += 1
    conn.orders_total += result.orders_inserted
    conn.spend_rows_total += result.spend_rows
    conn.last_synced_at = datetime.now(timezone.utc)
    conn.last_summary = result.summary[:500]
    session.commit()


# ---------------------------------------------------------------- inbound webhook

def orders_from_webhook(payload: dict) -> bytes:
    """Flatten {"orders": [{..., "lines": [{sku, quantity, unit_price, ...}]}]} into canonical CSV."""
    orders = payload.get("orders")
    if not isinstance(orders, list) or not orders:
        raise ConnectorError('Body must be {"orders": [ ... ]} with at least one order')
    if len(orders) > MAX_WEBHOOK_ORDERS:
        raise ConnectorError(f"At most {MAX_WEBHOOK_ORDERS} orders per request")
    rows = []
    for i, o in enumerate(orders):
        lines = o.get("lines") if isinstance(o, dict) else None
        if not isinstance(lines, list) or not lines:
            raise ConnectorError(f"orders[{i}] needs a non-empty 'lines' list")
        for ln in lines:
            if not isinstance(ln, dict):
                raise ConnectorError(f"orders[{i}].lines must be objects")
            g = lambda d, k, default="": d.get(k, default) if d.get(k) is not None else default  # noqa: E731
            rows.append([g(o, "order_id"), g(o, "order_date"), g(o, "customer_id"), g(ln, "sku"),
                         g(ln, "quantity"), g(ln, "unit_price"), g(ln, "discount", 0), g(ln, "refund_amount", 0),
                         g(ln, "unit_cogs"), g(o, "status", "delivered"), g(o, "payment_method", "prepaid"),
                         g(o, "channel", "organic"), g(o, "shipping_cost", 0), g(o, "payment_fee", 0)])
    return _csv(ORDER_HEADER, rows)
