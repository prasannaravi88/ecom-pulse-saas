from datetime import date

import pytest
from sqlalchemy import func, select

from app.db.models import DailyAdSpend, Order, OrderItem, Product
from app.services.analytics import AnalyticsEngine
from app.services.ingestion import IngestionError, ingest_ad_spend, ingest_orders

HEADER = ("order_id,order_date,customer_id,sku,quantity,unit_price,discount,refund_amount,unit_cogs,"
          "status,payment_method,channel,shipping_cost,payment_fee\n")


def _count(session, model, tenant):
    return session.scalar(select(func.count()).select_from(model).where(model.tenant_id == tenant))


def test_ingest_orders_and_metrics(session):
    csv = HEADER + (
        "W1,2026-03-01,c1,X,2,100,20,0,,delivered,prepaid,meta,50,10\n"
        "W1,2026-03-01,c1,NEW-1,1,300,0,0,120,delivered,prepaid,meta,50,10\n"
        "W2,2026-03-02,c2,X,1,100,0,0,,RTO,COD,Facebook,90,0\n"
        "W3,2026-03-03,c3,X,1,100,0,40,,partially_refunded,prepaid,google,40,3\n"
    )
    res = ingest_orders(session, "acme", csv.encode())
    assert (res.rows, res.inserted, res.replaced) == (4, 3, 0)
    assert res.products_created == ["NEW-1"]
    assert _count(session, Order, "acme") == 3 and _count(session, OrderItem, "acme") == 4
    w2 = session.scalar(select(Order).where(Order.tenant_id == "acme", Order.order_ref == "W2"))
    assert (w2.status, w2.payment_method, w2.attributed_channel) == ("rto", "cod", "meta")
    w1 = session.scalar(select(Order).where(Order.order_ref == "W1"))
    assert float(w1.shipping_cost) == 50  # order-level value repeated per line, counted once

    eng = AnalyticsEngine.for_tenant(session, "acme")
    try:
        p = eng.pnl(date(2026, 3, 1), date(2026, 3, 31))
    finally:
        eng.close()
    # gross 200+300+100+100 = 700; disc 20; returns: W2 RTO 100 + W3 refund 40 = 140
    assert p["net_revenue"] == pytest.approx(540)
    # cogs: X 2*30 (catalogue) + NEW-1 120 + W3 X 30 (W2 RTO restocked) = 210
    assert p["cogs"] == pytest.approx(210)
    assert p["shipping"] == pytest.approx(180) and p["payment_fees"] == pytest.approx(13)


def test_reupload_replaces_order(session):
    ingest_orders(session, "acme", (HEADER + "W1,2026-03-01,c1,X,1,100,0,0,,delivered,prepaid,meta,50,10\n").encode())
    res = ingest_orders(session, "acme", (HEADER + "W1,2026-03-01,c1,X,3,100,0,0,,delivered,prepaid,meta,50,10\n").encode())
    assert (res.inserted, res.replaced) == (0, 1)
    assert _count(session, Order, "acme") == 1
    assert session.scalar(select(OrderItem.quantity).where(OrderItem.tenant_id == "acme")) == 3


def test_shopify_export_headers(session):
    csv = ("Name,Created at,Email,Lineitem sku,Lineitem quantity,Lineitem price,Financial Status,Payment Method\n"
           "#1001,2026-03-05 10:22:01 +0530,a@x.com,X,1,100,paid,Cash on Delivery (COD)\n")
    ingest_orders(session, "acme", csv.encode())
    o = session.scalar(select(Order).where(Order.tenant_id == "acme"))
    assert (o.order_ref, o.order_date, o.status, o.payment_method) == ("#1001", date(2026, 3, 5), "delivered", "cod")


def test_validation_is_atomic_and_reports_rows(session):
    csv = HEADER + (
        "W1,2026-03-01,c1,X,1,100,0,0,,delivered,prepaid,meta,50,10\n"  # valid
        "W2,not-a-date,c2,X,1,100,0,0,,delivered,prepaid,meta,50,10\n"
        "W3,2026-03-01,c3,X,0,100,0,0,,delivered,prepaid,meta,50,10\n"
        "W4,2026-03-01,c4,X,1,abc,0,0,,delivered,prepaid,meta,50,10\n"
        "W5,2026-03-01,c5,X,1,100,150,0,,delivered,prepaid,meta,50,10\n"
        "W6,2026-03-01,c6,GHOST,1,100,0,0,,delivered,prepaid,meta,50,10\n"
        "W7,2026-03-01,c7,X,1,100,0,0,,lost,prepaid,meta,50,10\n"
        "W8,2026-03-01,c8,X,1,100,10,95,,refunded,prepaid,meta,50,10\n"
        "W1,2026-03-09,c1,X,1,100,0,0,,delivered,prepaid,meta,50,10\n"  # conflicts with line 2
    )
    with pytest.raises(IngestionError) as exc:
        ingest_orders(session, "acme", csv.encode())
    by_row = {(e["row"], e["column"]) for e in exc.value.errors}
    assert {(3, "order_date"), (4, "quantity"), (5, "unit_price"), (6, "discount"),
            (7, "unit_cogs"), (8, "status"), (9, "refund_amount"), (10, "order_id")} <= by_row
    session.rollback()
    assert _count(session, Order, "acme") == 0  # nothing written


def test_missing_columns(session):
    with pytest.raises(IngestionError) as exc:
        ingest_orders(session, "acme", b"order_id,sku\nW1,X\n")
    missing = {e["column"] for e in exc.value.errors}
    assert missing == {"order_date", "customer_id", "quantity", "unit_price"}


@pytest.mark.parametrize("content", [b"", b"order_id,order_date,customer_id,sku,quantity,unit_price\n"])
def test_empty_files_rejected(session, content):
    with pytest.raises(IngestionError):
        ingest_orders(session, "acme", content)


def test_ingest_is_tenant_scoped(session):
    # Same order_ref in another tenant must not be replaced, and SKU lookup is per-tenant.
    ingest_orders(session, "other", (HEADER + "W1,2026-03-01,c1,X,1,10,0,0,,delivered,prepaid,meta,5,1\n").encode())
    ingest_orders(session, "acme", (HEADER + "W1,2026-03-01,c1,X,1,100,0,0,,delivered,prepaid,meta,50,10\n").encode())
    assert _count(session, Order, "other") == 1 and _count(session, Order, "acme") == 1
    cogs = dict(session.execute(select(OrderItem.tenant_id, OrderItem.unit_cogs)).all())
    assert float(cogs["acme"]) == 30 and float(cogs["other"]) == 9
    # A SKU created by acme's upload is unknown to "other" (needs its own unit_cogs).
    ingest_orders(session, "acme", (HEADER + "W2,2026-03-01,c1,ACME-ONLY,1,1,0,0,1,delivered,prepaid,meta,0,0\n").encode())
    with pytest.raises(IngestionError):
        ingest_orders(session, "other", (HEADER + "W9,2026-03-01,c1,ACME-ONLY,1,1,0,0,,delivered,prepaid,meta,0,0\n").encode())
    session.rollback()
    assert session.scalar(select(func.count()).select_from(Product).where(Product.sku == "ACME-ONLY")) == 1


def test_ad_spend_upsert(session):
    csv = ("date,channel,campaign,spend,sku,impressions,clicks,platform_reported_revenue\n"
           "2026-03-01,meta,P-X,1000,X,50000,600,4000\n"
           "2026-03-01,Google Ads,Brand,250.50,,8000,300,900\n")
    res = ingest_ad_spend(session, "acme", csv.encode())
    assert (res.inserted, res.replaced) == (2, 0)
    res = ingest_ad_spend(session, "acme", b"Day,Platform,Campaign Name,Amount Spent\n2026-03-01,facebook,P-X,1200\n")
    assert (res.inserted, res.replaced) == (0, 1)
    rows = {r.campaign: r for r in session.scalars(select(DailyAdSpend).where(DailyAdSpend.tenant_id == "acme"))}
    assert float(rows["P-X"].spend) == 1200 and rows["Brand"].channel == "google"


def test_ad_spend_validation(session):
    csv = ("date,channel,campaign,spend\n"
           "2026-03-01,meta,A,-5\n"
           "2026-03-01,meta,,10\n"
           "2026-03-01,meta,B,10\n"
           "2026-03-01,meta,B,12\n")
    with pytest.raises(IngestionError) as exc:
        ingest_ad_spend(session, "acme", csv.encode())
    assert {(e["row"], e["column"]) for e in exc.value.errors} == {(2, "spend"), (3, "campaign"), (5, "campaign")}
    session.rollback()
    assert _count(session, DailyAdSpend, "acme") == 0
