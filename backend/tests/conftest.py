import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="ecom_pulse_test_")
# Set TEST_DATABASE_URL to run the API suite against PostgreSQL (must be an empty DB).
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", f"sqlite:///{_tmp}/test.db")
os.environ["SEED_ON_STARTUP"] = "false"
os.environ["ALLOWED_ORIGINS"] = "https://demo.example.com, https://pulse.example.com/"
os.environ["ALLOWED_ORIGIN_REGEX"] = r"https://ecom-pulse-[a-z0-9-]+\.vercel\.app"
os.environ["DEMO_MODE"] = "false"  # demo tests switch it on per-test

from datetime import date  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.db.models import Base, Product, Tenant  # noqa: E402
from app.db.session import make_engine  # noqa: E402

SEED_END = date(2026, 11, 20)


@pytest.fixture
def session(tmp_path):
    """Isolated SQLite DB per test with two empty tenants."""
    engine = make_engine(f"sqlite:///{tmp_path}/unit.db")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, expire_on_commit=False)()
    s.add_all([Tenant(id="acme", name="Acme", currency="INR"),
               Tenant(id="other", name="Other", currency="USD")])
    s.commit()
    s.add(Product(tenant_id="acme", sku="X", name="Serum X", category="Serums", price=100, unit_cogs=30))
    s.add(Product(tenant_id="other", sku="X", name="Other X", category="Misc", price=10, unit_cogs=9))
    s.commit()
    yield s
    s.close()
    engine.dispose()


@pytest.fixture(scope="session")
def client():
    """FastAPI TestClient over a DB seeded once with both demo tenants (ends SEED_END)."""
    from fastapi.testclient import TestClient

    from app.db.session import SessionLocal, init_db
    from app.main import app
    from app.services.seed import seed_all

    init_db()
    with SessionLocal() as s:
        seed_all(s, end=SEED_END)
    with TestClient(app) as c:
        yield c
