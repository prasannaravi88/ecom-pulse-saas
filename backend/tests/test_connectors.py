"""Local simulator connectors and the inbound webhook."""

import pytest
from datetime import date

from sqlalchemy import delete, func, select

from app.db.models import DailyAdSpend, Order, Product, Tenant
from app.db.session import SessionLocal
from app.services.seed import PROFILES

INR, USD = PROFILES[0].tenant_id, PROFILES[1].tenant_id
SIM = "sim-test"


@pytest.fixture(scope="module", autouse=True)
def sim_tenant(client):
    """A throwaway copy of the INR catalog so these tests never alter the shared seeded tenants."""
    with SessionLocal() as s:
        s.add(Tenant(id=SIM, name="Sim test", currency="INR"))
        s.flush()
        for p in s.scalars(select(Product).where(Product.tenant_id == INR)):
            s.add(Product(tenant_id=SIM, sku=p.sku, name=p.name, category=p.category, price=p.price, unit_cogs=p.unit_cogs))
        s.add(Order(tenant_id=SIM, order_ref="BASE-1", order_date=date(2026, 11, 20), customer_ref="c0"))
        s.commit()
    yield
    with SessionLocal() as s:
        s.execute(delete(Tenant).where(Tenant.id == SIM))
        s.commit()


def _h(t=SIM):
    return {"X-Tenant-ID": t}


def _kpis(client, t=SIM):
    return client.get("/api/metrics/overview?range=7D", headers=_h(t)).json()["kpis"]


def test_lists_four_providers(client):
    body = client.get("/api/connections", headers=_h()).json()
    assert [c["provider"] for c in body["connections"]] == ["shopify", "meta", "google", "webhook"]
    assert all(c["modes"] == ["simulator"] for c in body["connections"])


def test_shopify_sync_adds_orders_and_moves_numbers(client):
    before = _kpis(client)["net_revenue"]["value"] or 0
    r = client.post("/api/connections/shopify/sync", headers=_h(), json={"mode": "simulator"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["orders_inserted"] > 0 and body["sync_number"] == 1
    assert _kpis(client)["net_revenue"]["value"] > before
    again = client.post("/api/connections/shopify/sync", headers=_h()).json()
    assert again["sync_number"] == 2  # batches accumulate
    state = {c["provider"]: c for c in client.get("/api/connections", headers=_h()).json()["connections"]}
    assert state["shopify"]["connected"] and state["shopify"]["sync_count"] == 2
    assert state["meta"]["connected"] is False


@pytest.mark.parametrize("provider,channel", [("meta", "meta"), ("google", "google")])
def test_ad_sync_accumulates_daily_spend(client, provider, channel):
    def total():
        with SessionLocal() as s:
            return float(s.scalar(select(func.coalesce(func.sum(DailyAdSpend.spend), 0)).where(
                DailyAdSpend.tenant_id == SIM, DailyAdSpend.channel == channel,
                DailyAdSpend.campaign.like("%Simulator%"))))
    a = total()
    assert client.post(f"/api/connections/{provider}/sync", headers=_h()).status_code == 200
    b = total()
    assert client.post(f"/api/connections/{provider}/sync", headers=_h()).status_code == 200
    assert 0 < b - a and b - a < total() - a  # second batch adds to the running total


def test_sync_is_tenant_scoped(client):
    with SessionLocal() as s:
        n = lambda t: s.scalar(select(func.count()).select_from(Order).where(Order.tenant_id == t))  # noqa: E731
        before = (n(INR), n(USD))
    assert client.post("/api/connections/webhook/sync", headers=_h()).status_code == 200
    with SessionLocal() as s:
        assert (n(INR), n(USD)) == before


def test_unknown_provider_and_live_mode_rejected(client):
    assert client.post("/api/connections/stripe/sync", headers=_h()).status_code == 404
    r = client.post("/api/connections/shopify/sync", headers=_h(), json={"mode": "live"})
    assert r.status_code == 400 and "Local Simulator" in r.json()["detail"]
    assert client.post("/api/connections/shopify/sync").status_code == 400  # tenant header missing


def test_webhook_ingests_and_validates(client):
    order = {"order_id": "WH-T-1", "order_date": "2026-11-20", "customer_id": "c1", "channel": "meta",
             "lines": [{"sku": "GLW-VC-30", "quantity": 2, "unit_price": 799}]}
    r = client.post("/api/webhooks/orders", headers=_h(), json={"orders": [order]})
    assert r.status_code == 200 and r.json()["orders_inserted"] == 1
    r = client.post("/api/webhooks/orders", headers=_h(), json={"orders": [order]})
    assert r.json()["orders_replaced"] == 1  # idempotent on order_id
    bad = client.post("/api/webhooks/orders", headers=_h(), json={"orders": [{**order, "order_id": "WH-T-2", "lines": [{"sku": "NOPE", "quantity": 1, "unit_price": 5}]}]})
    assert bad.status_code == 422
    assert client.post("/api/webhooks/orders", headers=_h(), json={"orders": []}).status_code == 422
    assert client.post("/api/webhooks/orders", headers=_h(), json={}).status_code == 422
