"""Relational schema. Every tenant-owned table carries a non-null, indexed `tenant_id`.

Money columns are Numeric(14, 2) in the tenant's own currency (INR or USD).
Derived metrics (net revenue, CM1, CM2, ...) are never stored; they are computed
by `app.services.analytics` from these raw facts so a CSV upload is reflected
immediately.
"""

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

EMBEDDING_DIM = 384
Money = Numeric(14, 2)


# Set by init_db: False when the Postgres server has no pgvector extension (e.g. a plain
# managed Postgres). Embeddings then fall back to a JSON column; nothing else changes.
VECTOR_AVAILABLE = True


class Embedding(TypeDecorator):
    """pgvector `vector(n)` on PostgreSQL with pgvector; JSON list elsewhere (SQLite, plain Postgres)."""

    impl = JSON
    cache_ok = True

    def __init__(self, dim: int = EMBEDDING_DIM):
        super().__init__()
        self.dim = dim

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql" and VECTOR_AVAILABLE:
            from pgvector.sqlalchemy import Vector

            return dialect.type_descriptor(Vector(self.dim))
        return dialect.type_descriptor(JSON(none_as_null=True))

    def process_result_value(self, value, dialect):
        return None if value is None else list(value)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # slug
    name: Mapped[str] = mapped_column(String(200))
    currency: Mapped[str] = mapped_column(String(3))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (CheckConstraint("currency IN ('INR', 'USD')", name="ck_tenant_currency"),)


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    sku: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(100), default="Uncategorised")
    price: Mapped[Decimal] = mapped_column(Money)
    unit_cogs: Mapped[Decimal] = mapped_column(Money)
    embedding: Mapped[list[float] | None] = mapped_column(Embedding(), nullable=True)

    __table_args__ = (UniqueConstraint("tenant_id", "sku", name="uq_product_tenant_sku"),)


class Order(Base):
    """Order header. Shipping and payment fees are order-level costs; line amounts live on items."""

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    order_ref: Mapped[str] = mapped_column(String(64))
    order_date: Mapped[date] = mapped_column(Date)
    customer_ref: Mapped[str] = mapped_column(String(128))
    # delivered | rto (returned to origin, never accepted) | refunded | cancelled (never shipped)
    status: Mapped[str] = mapped_column(String(16), default="delivered")
    payment_method: Mapped[str] = mapped_column(String(16), default="prepaid")  # prepaid | cod
    # Last-touch channel the order is attributed to: meta | google | organic | ...
    attributed_channel: Mapped[str] = mapped_column(String(32), default="organic")
    shipping_cost: Mapped[Decimal] = mapped_column(Money, default=0)
    payment_fee: Mapped[Decimal] = mapped_column(Money, default=0)

    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "order_ref", name="uq_order_tenant_ref"),
        Index("ix_orders_tenant_date", "tenant_id", "order_date"),
        Index("ix_orders_tenant_customer", "tenant_id", "customer_ref"),
        CheckConstraint(
            "status IN ('delivered', 'rto', 'refunded', 'cancelled')", name="ck_order_status"
        ),
        CheckConstraint("payment_method IN ('prepaid', 'cod')", name="ck_order_payment"),
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    sku: Mapped[str] = mapped_column(String(64))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[Decimal] = mapped_column(Money)
    discount: Mapped[Decimal] = mapped_column(Money, default=0)  # line-level discount total
    # Refunded amount for delivered-then-refunded lines. RTO lines are reversed in
    # full by the analytics engine, so this stays 0 for them.
    refund_amount: Mapped[Decimal] = mapped_column(Money, default=0)
    unit_cogs: Mapped[Decimal] = mapped_column(Money)  # snapshot at time of sale

    order: Mapped[Order] = relationship(back_populates="items")

    __table_args__ = (
        Index("ix_items_tenant_sku", "tenant_id", "sku"),
        CheckConstraint("quantity > 0", name="ck_item_qty"),
    )


class DailyAdSpend(Base):
    __tablename__ = "daily_ad_spend"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    spend_date: Mapped[date] = mapped_column(Date)
    channel: Mapped[str] = mapped_column(String(32))  # meta | google
    campaign: Mapped[str] = mapped_column(String(200))
    # Optional SKU the campaign promotes; unmapped spend is allocated pro-rata by net revenue.
    sku: Mapped[str | None] = mapped_column(String(64), nullable=True)
    spend: Mapped[Decimal] = mapped_column(Money)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    # Revenue the ad platform claims credit for (inflated; used only for comparison).
    platform_reported_revenue: Mapped[Decimal] = mapped_column(Money, default=0)

    __table_args__ = (
        UniqueConstraint("tenant_id", "spend_date", "channel", "campaign", name="uq_spend_row"),
        Index("ix_spend_tenant_date", "tenant_id", "spend_date"),
    )


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    severity: Mapped[str] = mapped_column(String(16))  # info | warning | critical
    kind: Mapped[str] = mapped_column(String(32))  # margin_leak | rto_spike | ...
    subject: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(String(1000))


class Sandbox(Base):
    """A visitor's private copy of a seeded tenant (DEMO_MODE). Expires after SANDBOX_TTL_HOURS."""

    __tablename__ = "sandboxes"

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    base_tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (UniqueConstraint("base_tenant_id", "session_id", name="uq_sandbox_session"),)
