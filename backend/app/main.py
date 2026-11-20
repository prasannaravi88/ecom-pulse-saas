import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.api import routes
from app.config import get_settings
from app.db.models import Tenant
from app.db.session import SessionLocal, init_db
from app.services.alerts import refresh_alerts
from app.services.analytics import AnalyticsEngine
from app.services.sandbox import cleanup_expired
from app.services.seed import seed_all

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ecom_pulse")


def bootstrap() -> None:
    settings = get_settings()
    init_db()
    with SessionLocal() as session:
        if settings.demo_mode:
            dropped = cleanup_expired(session)
            if dropped:
                log.info("Removed %d expired demo sandboxes", len(dropped))
        if not settings.seed_on_startup:
            return
        seeded = seed_all(session)
        for tenant_id in seeded:
            engine = AnalyticsEngine.for_tenant(session, tenant_id)
            try:
                refresh_alerts(session, tenant_id, engine)
            finally:
                engine.close()
        if seeded:
            log.info("Seeded demo tenants: %s", ", ".join(seeded))
        else:
            log.info("Tenants present: %s", ", ".join(session.scalars(select(Tenant.id)).all()))


@asynccontextmanager
async def lifespan(_: FastAPI):
    bootstrap()
    yield
    routes.invalidate()


app = FastAPI(title="Ecom Pulse API", version="0.1.0", lifespan=lifespan)
_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.origins,
    allow_origin_regex=_settings.allowed_origin_regex or None,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["X-Tenant-ID", "X-Sandbox-ID", "Content-Type"],
    max_age=600,
)
app.include_router(routes.router)
