from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Annotated

from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Alert, Sandbox, Tenant
from app.db.session import get_session
from app.services import analyst, ingestion, sandbox
from app.services.alerts import refresh_alerts
from app.services.analytics import AnalyticsEngine, Period, PeriodPreset, custom_period, resolve_period

router = APIRouter(prefix="/api")

# ---------------------------------------------------------------- engine cache
# One in-memory DuckDB engine per tenant (sandboxes included), rebuilt after that tenant's
# data changes. LRU-capped because every demo sandbox is its own tenant.
MAX_CACHED_ENGINES = 24
_engines: OrderedDict[str, AnalyticsEngine] = OrderedDict()
_engines_lock = threading.Lock()


def get_engine(session: Session, tenant_id: str) -> AnalyticsEngine:
    with _engines_lock:
        engine = _engines.get(tenant_id)
        if engine is None:
            engine = _engines[tenant_id] = AnalyticsEngine.for_tenant(session, tenant_id)
            while len(_engines) > MAX_CACHED_ENGINES:
                _engines.popitem(last=False)[1].close()
        _engines.move_to_end(tenant_id)
        return engine


def invalidate(tenant_id: str | None = None) -> None:
    with _engines_lock:
        targets = [tenant_id] if tenant_id else list(_engines)
        for tid in targets:
            engine = _engines.pop(tid, None)
            if engine is not None:
                engine.close()


# ---------------------------------------------------------------- dependencies

SessionDep = Annotated[Session, Depends(get_session)]


@dataclass(frozen=True)
class ActiveTenant:
    id: str  # tenant whose rows are read/written (a sandbox copy when one is active)
    public_id: str  # the seeded/real tenant the client asked for
    name: str
    currency: str
    sandboxed: bool


def _base_tenant(session: Session, tenant_id: str | None) -> Tenant:
    if not tenant_id:
        raise HTTPException(400, "Missing tenant: send the X-Tenant-ID header")
    row = session.get(Tenant, tenant_id)
    # Sandbox copies are never addressable directly, only via their owner's X-Sandbox-ID.
    if row is None or sandbox.is_sandbox_id(session, tenant_id):
        raise HTTPException(404, f"Unknown tenant {tenant_id!r}")
    return row


def _active(base: Tenant, data_id: str) -> ActiveTenant:
    return ActiveTenant(id=data_id, public_id=base.id, name=base.name, currency=base.currency,
                        sandboxed=data_id != base.id)


def current_tenant(
    session: SessionDep,
    x_tenant_id: Annotated[str | None, Header()] = None,
    tenant: Annotated[str | None, Query(description="Alternative to the X-Tenant-ID header")] = None,
    x_sandbox_id: Annotated[str | None, Header()] = None,
) -> ActiveTenant:
    """Read scope: the visitor's sandbox copy if one exists (DEMO_MODE), else the tenant itself."""
    base = _base_tenant(session, x_tenant_id or tenant)
    if get_settings().demo_mode and sandbox.valid_session_id(x_sandbox_id):
        sb = sandbox.find(session, base.id, x_sandbox_id)
        if sb is not None:
            return _active(base, sb.tenant_id)
    return _active(base, base.id)


def write_tenant(
    session: SessionDep,
    x_tenant_id: Annotated[str | None, Header()] = None,
    tenant: Annotated[str | None, Query()] = None,
    x_sandbox_id: Annotated[str | None, Header()] = None,
) -> ActiveTenant:
    """Write scope. In DEMO_MODE seeded data is never modified: writes go to (and create) the
    visitor's private sandbox, so they need an X-Sandbox-ID."""
    base = _base_tenant(session, x_tenant_id or tenant)
    if not get_settings().demo_mode:
        return _active(base, base.id)
    if not sandbox.valid_session_id(x_sandbox_id):
        raise HTTPException(403, "Demo mode: uploads go to a private sandbox. Send an X-Sandbox-ID header "
                                 "(16-64 letters, digits or dashes).")
    return _active(base, sandbox.ensure(session, base, x_sandbox_id))


TenantDep = Annotated[ActiveTenant, Depends(current_tenant)]
WriteTenantDep = Annotated[ActiveTenant, Depends(write_tenant)]


def period_params(
    preset: Annotated[PeriodPreset, Query(alias="range")] = "30D",
    start: date | None = None,
    end: date | None = None,
) -> tuple[PeriodPreset, date | None, date | None]:
    if (start is None) != (end is None):
        raise HTTPException(422, "Provide both start and end, or neither")
    if start and end and start > end:
        raise HTTPException(422, "start must be on or before end")
    return preset, start, end


PeriodDep = Annotated[tuple, Depends(period_params)]


def _resolve(engine: AnalyticsEngine, params: tuple) -> Period:
    preset, start, end = params
    return custom_period(start, end) if start else resolve_period(preset, engine.as_of())


def _period_meta(period: Period, params: tuple) -> dict:
    return {"range": None if params[1] else params[0], "start": period.start.isoformat(),
            "end": period.end.isoformat(), "previous_start": period.prev_start.isoformat(),
            "previous_end": period.prev_end.isoformat(), "days": period.days}


# ---------------------------------------------------------------- read endpoints


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/tenants")
def list_tenants(session: SessionDep) -> list[dict]:
    rows = session.scalars(
        select(Tenant).where(Tenant.id.not_in(select(Sandbox.tenant_id))).order_by(Tenant.name)
    ).all()
    return [{"id": t.id, "name": t.name, "currency": t.currency} for t in rows]


@router.get("/metrics/overview")
def overview(tenant: TenantDep, session: SessionDep, params: PeriodDep) -> dict:
    engine = get_engine(session, tenant.id)
    period = _resolve(engine, params)
    return {"tenant": tenant.public_id, "currency": tenant.currency,
            "period": _period_meta(period, params), **engine.overview(period)}


@router.get("/metrics/daily")
def daily(tenant: TenantDep, session: SessionDep, params: PeriodDep) -> dict:
    engine = get_engine(session, tenant.id)
    period = _resolve(engine, params)
    return {"tenant": tenant.public_id, "currency": tenant.currency,
            "period": _period_meta(period, params), "days": engine.daily(period.start, period.end)}


@router.get("/metrics/channels")
def channels(tenant: TenantDep, session: SessionDep, params: PeriodDep) -> dict:
    engine = get_engine(session, tenant.id)
    period = _resolve(engine, params)
    return {"tenant": tenant.public_id, "currency": tenant.currency,
            "period": _period_meta(period, params), "channels": engine.channels(period.start, period.end)}


@router.get("/metrics/skus")
def skus(tenant: TenantDep, session: SessionDep, params: PeriodDep) -> dict:
    engine = get_engine(session, tenant.id)
    period = _resolve(engine, params)
    return {"tenant": tenant.public_id, "currency": tenant.currency,
            "period": _period_meta(period, params), **engine.skus(period.start, period.end)}


@router.get("/metrics/payment-mix")
def payment_mix(tenant: TenantDep, session: SessionDep, params: PeriodDep) -> dict:
    engine = get_engine(session, tenant.id)
    period = _resolve(engine, params)
    return {"tenant": tenant.public_id, "currency": tenant.currency,
            "period": _period_meta(period, params), "payment_mix": engine.payment_mix(period.start, period.end)}


@router.get("/fx")
def fx() -> dict:
    return {"base": "USD", "quote": "INR", "rate": get_settings().usd_inr_rate,
            "note": "Static display rate; stored figures stay in each tenant's own currency."}


# ---------------------------------------------------------------- analyst (read-only)


class AskRequest(BaseModel):
    question: str | None = Field(default=None, max_length=2000)
    prompt_id: str | None = None
    range: PeriodPreset = "30D"
    display_currency: str | None = Field(default=None, pattern="^(INR|USD)$")


@router.get("/analyst/prompts")
def analyst_prompts() -> list[dict]:
    return analyst.prompts()


@router.post("/analyst/ask")
def analyst_ask(body: AskRequest, tenant: TenantDep, session: SessionDep) -> dict:
    engine = get_engine(session, tenant.id)
    ctx = analyst.Ctx(engine, resolve_period(body.range, engine.as_of()), tenant.currency,
                      body.display_currency or tenant.currency, get_settings().usd_inr_rate)
    return {"tenant": tenant.public_id, "currency": tenant.currency,
            **analyst.ask(ctx, question=body.question, prompt_id=body.prompt_id)}


@router.get("/cohorts")
def cohorts(tenant: TenantDep, session: SessionDep) -> dict:
    engine = get_engine(session, tenant.id)
    return {"tenant": tenant.public_id, "currency": tenant.currency, **engine.cohorts()}


@router.get("/alerts")
def alerts(tenant: TenantDep, session: SessionDep) -> list[dict]:
    rows = session.scalars(
        select(Alert).where(Alert.tenant_id == tenant.id).order_by(Alert.created_at.desc()).limit(100)
    ).all()
    return [{"id": a.id, "created_at": a.created_at.isoformat(), "severity": a.severity,
             "kind": a.kind, "subject": a.subject, "message": a.message} for a in rows]


# ---------------------------------------------------------------- ingestion


async def _read_upload(file: UploadFile) -> bytes:
    limit = get_settings().max_upload_bytes
    content = await file.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(413, f"File larger than {limit // (1024 * 1024)} MB")
    if file.filename and not file.filename.lower().endswith(".csv"):
        raise HTTPException(415, "Upload a .csv file")
    return content


def _validation_error(exc: ingestion.IngestionError) -> HTTPException:
    return HTTPException(422, {"message": str(exc), "errors": exc.errors, "columns_found": exc.columns})


@router.post("/ingest/orders")
async def upload_orders(tenant: WriteTenantDep, session: SessionDep, file: UploadFile = File(...)) -> dict:
    content = await _read_upload(file)
    try:
        result = ingestion.ingest_orders(session, tenant.id, content)
    except ingestion.IngestionError as exc:
        session.rollback()
        raise _validation_error(exc)
    invalidate(tenant.id)
    refresh_alerts(session, tenant.id, get_engine(session, tenant.id))
    return {"tenant": tenant.public_id, "sandboxed": tenant.sandboxed, "rows": result.rows, "orders_inserted": result.inserted,
            "orders_replaced": result.replaced, "products_created": result.products_created}


@router.post("/ingest/ad-spend")
async def upload_ad_spend(tenant: WriteTenantDep, session: SessionDep, file: UploadFile = File(...)) -> dict:
    content = await _read_upload(file)
    try:
        result = ingestion.ingest_ad_spend(session, tenant.id, content)
    except ingestion.IngestionError as exc:
        session.rollback()
        raise _validation_error(exc)
    invalidate(tenant.id)
    refresh_alerts(session, tenant.id, get_engine(session, tenant.id))
    return {"tenant": tenant.public_id, "sandboxed": tenant.sandboxed, "rows": result.rows, "rows_inserted": result.inserted,
            "rows_replaced": result.replaced}


@router.get("/ingest/templates/{kind}", response_class=PlainTextResponse)
def template(kind: str) -> PlainTextResponse:
    body = {"orders": ingestion.ORDERS_TEMPLATE, "ad-spend": ingestion.SPEND_TEMPLATE}.get(kind)
    if body is None:
        raise HTTPException(404, "Template must be 'orders' or 'ad-spend'")
    return PlainTextResponse(body, media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{kind}-template.csv"'})


@router.get("/ingest/schema")
def ingest_schema() -> dict:
    return {
        "orders": {"required": ingestion.ORDER_REQUIRED, "optional": list(ingestion.ORDER_OPTIONAL),
                   "aliases": ingestion.ORDER_ALIASES},
        "ad_spend": {"required": ingestion.SPEND_REQUIRED, "optional": list(ingestion.SPEND_OPTIONAL),
                     "aliases": ingestion.SPEND_ALIASES},
    }


# ---------------------------------------------------------------- demo sandbox


@router.get("/demo/status")
def demo_status(session: SessionDep, x_sandbox_id: Annotated[str | None, Header()] = None) -> dict:
    settings = get_settings()
    active = []
    if settings.demo_mode and sandbox.valid_session_id(x_sandbox_id):
        ttl = timedelta(hours=settings.sandbox_ttl_hours)
        for sb in sandbox.list_for_session(session, x_sandbox_id):
            created = sandbox._aware(sb.created_at)
            active.append({"tenant": sb.base_tenant_id, "created_at": created.isoformat(),
                           "expires_at": (created + ttl).isoformat()})
    return {"demo_mode": settings.demo_mode, "sandbox_ttl_hours": settings.sandbox_ttl_hours,
            "active_sandboxes": active}


@router.post("/demo/reset")
def demo_reset(session: SessionDep, x_sandbox_id: Annotated[str | None, Header()] = None) -> dict:
    """Discard this browser's sandbox copies; the visitor sees the clean seed data again."""
    if not get_settings().demo_mode:
        raise HTTPException(404, "Demo mode is off")
    if not sandbox.valid_session_id(x_sandbox_id):
        raise HTTPException(400, "Send the X-Sandbox-ID header of the sandbox to reset")
    ids = [sb.tenant_id for sb in session.scalars(select(Sandbox).where(Sandbox.session_id == x_sandbox_id))]
    bases = sandbox.reset(session, x_sandbox_id)
    for tid in ids:
        invalidate(tid)
    return {"reset": bases}
