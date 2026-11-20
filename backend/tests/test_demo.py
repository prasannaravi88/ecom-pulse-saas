"""DEMO_MODE sandboxes, environment parsing and CORS."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.config import Settings, get_settings, normalize_database_url
from app.db.models import Order, Sandbox
from app.db.session import SessionLocal
from app.services import sandbox
from app.services.analytics import AnalyticsEngine
from app.services.seed import PROFILES
from tests.conftest import SEED_END

INR = PROFILES[0].tenant_id
ROW = ("order_id,order_date,customer_id,sku,quantity,unit_price\n"
       f"SBX-1,{SEED_END},sandbox-cust,SUN-SPF50,1,649\n")


@pytest.fixture
def demo(monkeypatch, client):
    monkeypatch.setattr(get_settings(), "demo_mode", True)
    yield client


def sid() -> str:
    return str(uuid.uuid4())


def overview(client, sandbox_id=None, tenant=INR):
    headers = {"X-Tenant-ID": tenant} | ({"X-Sandbox-ID": sandbox_id} if sandbox_id else {})
    r = client.get("/api/metrics/overview?range=7D", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["kpis"]


def upload(client, sandbox_id=None, body=ROW, tenant=INR):
    headers = {"X-Tenant-ID": tenant} | ({"X-Sandbox-ID": sandbox_id} if sandbox_id else {})
    return client.post("/api/ingest/orders", headers=headers, files={"file": ("o.csv", body, "text/csv")})


def base_order_count(tenant=INR) -> int:
    with SessionLocal() as s:
        return s.scalar(select(func.count()).select_from(Order).where(Order.tenant_id == tenant))


# ------------------------------------------------------------------ settings


@pytest.mark.parametrize("raw,expected", [
    ("postgres://u:p@h:5432/db", "postgresql+psycopg://u:p@h:5432/db"),
    ("postgresql://u:p@h/db?sslmode=require", "postgresql+psycopg://u:p@h/db?sslmode=require"),
    ("postgresql+psycopg://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
    ("sqlite:///./x.db", "sqlite:///./x.db"),
])
def test_database_url_normalised(raw, expected):
    assert normalize_database_url(raw) == expected
    assert Settings(database_url=raw).database_url == expected


def test_allowed_origins_parsing():
    s = Settings(allowed_origins=" https://a.vercel.app/ ,https://b.example.com,, ")
    assert s.origins == ["https://a.vercel.app", "https://b.example.com"]
    assert Settings(allowed_origins="").origins == []


def test_demo_mode_from_env(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "true")
    assert Settings().demo_mode is True


@pytest.mark.parametrize("origin,allowed", [
    ("https://demo.example.com", True),
    ("https://pulse.example.com", True),  # trailing slash in ALLOWED_ORIGINS is tolerated
    ("https://ecom-pulse-git-feature-x-team.vercel.app", True),  # preview regex
    ("https://evil.example.com", False),
    ("http://localhost:3000", False),
])
def test_cors(client, origin, allowed):
    r = client.options("/api/metrics/overview", headers={
        "Origin": origin, "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "x-tenant-id,x-sandbox-id"})
    assert (r.headers.get("access-control-allow-origin") == origin) is allowed
    if allowed:
        assert "x-sandbox-id" in r.headers["access-control-allow-headers"].lower()


# ------------------------------------------------------------------ sandbox behaviour


def test_demo_off_by_default(client):
    assert client.get("/api/demo/status").json()["demo_mode"] is False
    assert client.post("/api/demo/reset", headers={"X-Sandbox-ID": sid()}).status_code == 404


def test_seed_is_read_only_without_sandbox(demo):
    before = base_order_count()
    r = upload(demo)
    assert r.status_code == 403 and "sandbox" in r.json()["detail"].lower()
    assert upload(demo, sandbox_id="short").status_code == 403  # malformed id
    assert base_order_count() == before


def test_sandbox_isolation_and_reset(demo):
    a, b = sid(), sid()
    seed_kpis = overview(demo)
    seed_orders = base_order_count()

    r = upload(demo, a)
    assert r.status_code == 200, r.text
    assert r.json()["sandboxed"] is True and r.json()["tenant"] == INR

    # Browser A sees its upload; the shared seed and browser B do not.
    assert overview(demo, a)["orders"]["value"] == seed_kpis["orders"]["value"] + 1
    assert overview(demo, a)["net_revenue"]["value"] == pytest.approx(seed_kpis["net_revenue"]["value"] + 649)
    assert overview(demo)["orders"]["value"] == seed_kpis["orders"]["value"]
    assert overview(demo, b)["orders"]["value"] == seed_kpis["orders"]["value"]
    assert base_order_count() == seed_orders

    # Sandbox tenants are invisible and not directly addressable.
    tenants = [t["id"] for t in demo.get("/api/tenants").json()]
    assert all(sandbox.SEP not in t for t in tenants) and len(tenants) == 2
    sb_id = sandbox.sandbox_tenant_id(INR, a)
    assert demo.get("/api/metrics/overview", headers={"X-Tenant-ID": sb_id}).status_code == 404
    assert a not in sb_id  # raw browser id never leaks into tenant ids

    status = demo.get("/api/demo/status", headers={"X-Sandbox-ID": a}).json()
    assert status["demo_mode"] is True and [s["tenant"] for s in status["active_sandboxes"]] == [INR]

    # A second upload reuses the same sandbox (re-uploading an order_id replaces it).
    assert upload(demo, a).status_code == 200
    assert overview(demo, a)["orders"]["value"] == seed_kpis["orders"]["value"] + 1

    r = demo.post("/api/demo/reset", headers={"X-Sandbox-ID": a})
    assert r.json() == {"reset": [INR]}
    assert overview(demo, a)["orders"]["value"] == seed_kpis["orders"]["value"]
    assert demo.get("/api/demo/status", headers={"X-Sandbox-ID": a}).json()["active_sandboxes"] == []
    with SessionLocal() as s:  # every cloned row is gone
        assert s.scalar(select(func.count()).select_from(Order).where(Order.tenant_id == sb_id)) == 0


def test_clone_is_exact(demo):
    """A fresh sandbox computes the same numbers as the seed it was cloned from."""
    with SessionLocal() as s:
        from app.db.models import Tenant

        sb_id = sandbox.ensure(s, s.get(Tenant, INR), sid())
        base, copy = AnalyticsEngine.for_tenant(s, INR), AnalyticsEngine.for_tenant(s, sb_id)
        try:
            start, end = SEED_END - timedelta(days=89), SEED_END
            # Raw sums differ only in float summation order (~1e-12 relative); served values are rounded.
            assert copy.pnl(start, end) == pytest.approx(base.pnl(start, end), rel=1e-9)
            assert copy.cohorts() == base.cohorts()
            assert copy.skus(start, end) == base.skus(start, end)
            assert copy.channels(start, end) == base.channels(start, end)
        finally:
            base.close()
            copy.close()
            sandbox.drop_tenants(s, [sb_id])
            s.commit()


def test_sandbox_expiry(demo):
    a = sid()
    assert upload(demo, a).status_code == 200
    sb_id = sandbox.sandbox_tenant_id(INR, a)
    with SessionLocal() as s:
        row = s.get(Sandbox, sb_id)
        row.created_at = datetime.now(timezone.utc) - timedelta(hours=25)
        s.commit()
    # Expired: reads fall back to the seed immediately, cleanup removes the rows.
    assert overview(demo, a)["orders"]["value"] == overview(demo)["orders"]["value"]
    with SessionLocal() as s:
        assert sb_id in sandbox.cleanup_expired(s)
        assert s.scalar(select(func.count()).select_from(Order).where(Order.tenant_id == sb_id)) == 0


def test_sandbox_cap_evicts_oldest(demo, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_sandboxes", 2)
    with SessionLocal() as s:  # start from an empty sandbox table
        sandbox.drop_tenants(s, list(s.scalars(select(Sandbox.tenant_id))))
        s.commit()
    ids = [sid() for _ in range(3)]
    for i, x in enumerate(ids):
        assert upload(demo, x).status_code == 200
        with SessionLocal() as s:  # make creation order unambiguous
            s.get(Sandbox, sandbox.sandbox_tenant_id(INR, x)).created_at = datetime.now(timezone.utc) - timedelta(minutes=10 - i)
            s.commit()
    with SessionLocal() as s:
        live = set(s.scalars(select(Sandbox.tenant_id)))
    assert sandbox.sandbox_tenant_id(INR, ids[0]) not in live
    assert {sandbox.sandbox_tenant_id(INR, x) for x in ids[1:]} <= live
    for x in ids:
        demo.post("/api/demo/reset", headers={"X-Sandbox-ID": x})


def test_writes_go_to_base_when_demo_off(client):
    """With DEMO_MODE=false (a real deployment), uploads write to the tenant itself."""
    usd = PROFILES[1].tenant_id
    before = base_order_count(usd)
    body = ("order_id,order_date,customer_id,sku,quantity,unit_price\n"
            f"REAL-1,{SEED_END},real-cust,CB-HOUSE-12,1,18\n")
    r = upload(client, sid(), body=body, tenant=usd)
    assert r.status_code == 200 and r.json()["sandboxed"] is False
    assert base_order_count(usd) == before + 1


def test_fresh_sandbox_loads_fast(demo):
    """Regression: on Postgres a just-cloned tenant has no planner stats; loading its analytics
    once took minutes via a nested-loop join. Clone + first load must stay interactive."""
    import time

    from app.db.models import Tenant

    with SessionLocal() as s:
        t0 = time.monotonic()
        sb_id = sandbox.ensure(s, s.get(Tenant, INR), sid())
        engine = AnalyticsEngine.for_tenant(s, sb_id)
        try:
            elapsed = time.monotonic() - t0
            assert engine.pnl(SEED_END - timedelta(days=29), SEED_END)["orders"] > 1000
        finally:
            engine.close()
            sandbox.drop_tenants(s, [sb_id])
            s.commit()
    assert elapsed < 15, f"clone + load took {elapsed:.1f}s"
