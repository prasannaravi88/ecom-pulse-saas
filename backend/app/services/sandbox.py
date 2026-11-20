"""Per-visitor demo sandboxes (DEMO_MODE=true).

The seeded tenants are the shared, read-only showcase. The first time a visitor
uploads a CSV, their browser's sandbox id (X-Sandbox-ID) gets a private copy of
that tenant, cloned inside the database with INSERT ... SELECT, and every later
read or write from that browser for that tenant is routed to the copy. "Reset
Demo" deletes the visitor's copies, so they see the clean seed again. Copies
expire after SANDBOX_TTL_HOURS and the total is capped at MAX_SANDBOXES (oldest
evicted first) so a public demo can't grow the database without bound.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import bindparam, delete, func, select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Sandbox, Tenant
from app.db.session import analyze

SESSION_ID_RE = re.compile(r"^[A-Za-z0-9-]{16,64}$")
SEP = "~"  # never valid in a seeded tenant slug, so sandbox ids can't be addressed directly


def valid_session_id(value: str | None) -> bool:
    return bool(value and SESSION_ID_RE.match(value))


def sandbox_tenant_id(base_tenant_id: str, session_id: str) -> str:
    # Hash the browser's id so it never appears in tenant ids, logs or URLs.
    digest = hashlib.sha256(session_id.encode()).hexdigest()[:16]
    return f"{base_tenant_id}{SEP}{digest}"


def _cutoff() -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=get_settings().sandbox_ttl_hours)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)  # SQLite drops tzinfo


def is_sandbox_id(session: Session, tenant_id: str) -> bool:
    return SEP in tenant_id or session.get(Sandbox, tenant_id) is not None


def find(session: Session, base_tenant_id: str, session_id: str) -> Sandbox | None:
    sb = session.scalar(select(Sandbox).where(Sandbox.base_tenant_id == base_tenant_id,
                                              Sandbox.session_id == session_id))
    if sb is None or _aware(sb.created_at) < _cutoff():
        return None
    return sb


def list_for_session(session: Session, session_id: str) -> list[Sandbox]:
    rows = session.scalars(select(Sandbox).where(Sandbox.session_id == session_id)).all()
    return [r for r in rows if _aware(r.created_at) >= _cutoff()]


def drop_tenants(session: Session, tenant_ids: list[str]) -> None:
    if not tenant_ids:
        return
    # Explicit child deletes (children first) so this works without relying on FK cascades.
    session.execute(delete(Sandbox).where(Sandbox.tenant_id.in_(tenant_ids)))
    for table in ("alerts", "daily_ad_spend", "order_items", "orders", "products"):
        stmt = text(f"DELETE FROM {table} WHERE tenant_id IN :ids").bindparams(bindparam("ids", expanding=True))
        session.execute(stmt, {"ids": tenant_ids})
    session.execute(delete(Tenant).where(Tenant.id.in_(tenant_ids)))


def cleanup_expired(session: Session) -> list[str]:
    expired = [sb.tenant_id for sb in session.scalars(select(Sandbox)).all() if _aware(sb.created_at) < _cutoff()]
    drop_tenants(session, expired)
    session.commit()
    return expired


def reset(session: Session, session_id: str) -> list[str]:
    """Delete every sandbox owned by this browser. Returns the base tenant ids that were reset."""
    rows = session.scalars(select(Sandbox).where(Sandbox.session_id == session_id)).all()
    bases = [r.base_tenant_id for r in rows]
    drop_tenants(session, [r.tenant_id for r in rows])
    session.commit()
    return bases


_CLONE = [
    ("products", "tenant_id, sku, name, category, price, unit_cogs, embedding",
     "SELECT :new, sku, name, category, price, unit_cogs, embedding FROM products WHERE tenant_id = :base"),
    ("orders", "tenant_id, order_ref, order_date, customer_ref, status, payment_method, attributed_channel, "
               "shipping_cost, payment_fee",
     "SELECT :new, order_ref, order_date, customer_ref, status, payment_method, attributed_channel, "
     "shipping_cost, payment_fee FROM orders WHERE tenant_id = :base"),
    # Re-link items to the cloned orders through the (tenant_id, order_ref) unique key.
    ("order_items", "tenant_id, order_id, sku, quantity, unit_price, discount, refund_amount, unit_cogs",
     "SELECT :new, o2.id, i.sku, i.quantity, i.unit_price, i.discount, i.refund_amount, i.unit_cogs "
     "FROM order_items i JOIN orders o ON o.id = i.order_id "
     "JOIN orders o2 ON o2.tenant_id = :new AND o2.order_ref = o.order_ref WHERE i.tenant_id = :base"),
    ("daily_ad_spend", "tenant_id, spend_date, channel, campaign, sku, spend, impressions, clicks, "
                       "platform_reported_revenue",
     "SELECT :new, spend_date, channel, campaign, sku, spend, impressions, clicks, platform_reported_revenue "
     "FROM daily_ad_spend WHERE tenant_id = :base"),
    ("alerts", "tenant_id, created_at, severity, kind, subject, message",
     "SELECT :new, created_at, severity, kind, subject, message FROM alerts WHERE tenant_id = :base"),
]


def ensure(session: Session, base: Tenant, session_id: str) -> str:
    """Return this browser's sandbox tenant id for `base`, cloning the seed data on first use."""
    existing = find(session, base.id, session_id)
    if existing is not None:
        return existing.tenant_id
    cleanup_expired(session)
    stale = session.scalar(select(Sandbox).where(Sandbox.base_tenant_id == base.id,
                                                 Sandbox.session_id == session_id))
    if stale is not None:
        drop_tenants(session, [stale.tenant_id])
    cap = get_settings().max_sandboxes
    count = session.scalar(select(func.count()).select_from(Sandbox)) or 0
    if count >= cap:
        oldest = session.scalars(select(Sandbox.tenant_id).order_by(Sandbox.created_at).limit(count - cap + 1)).all()
        drop_tenants(session, list(oldest))

    new_id = sandbox_tenant_id(base.id, session_id)
    session.add(Tenant(id=new_id, name=base.name, currency=base.currency))
    session.flush()
    for table, cols, select_sql in _CLONE:
        session.execute(text(f"INSERT INTO {table} ({cols}) {select_sql}"), {"new": new_id, "base": base.id})
    session.add(Sandbox(tenant_id=new_id, base_tenant_id=base.id, session_id=session_id))
    session.commit()
    analyze(session)
    return new_id
