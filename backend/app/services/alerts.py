"""Guardrail alerts derived from the canonical SKU rollup (last 30 days)."""

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db.models import Alert
from app.services.analytics import AnalyticsEngine, resolve_period

GUARDRAIL_KINDS = ("margin_leak",)


def refresh_alerts(session: Session, tenant_id: str, engine: AnalyticsEngine) -> int:
    period = resolve_period("30D", engine.as_of())
    leaks = [s for s in engine.skus(period.start, period.end)["skus"] if s["segment"] == "margin_leak"]
    session.execute(delete(Alert).where(Alert.tenant_id == tenant_id, Alert.kind.in_(GUARDRAIL_KINDS)))
    for s in leaks:
        session.add(Alert(
            tenant_id=tenant_id,
            severity="critical" if (s["cm2"] or 0) < 0 else "warning",
            kind="margin_leak",
            subject=s["sku"],
            message=(f"{s['name']}: CM2 {s['cm2']:,.0f} ({(s['cm2_pct'] or 0):.0%} of net revenue), "
                     f"refund/RTO {(s['return_rate'] or 0):.0%}, ad spend {s['ad_spend']:,.0f} "
                     f"over {period.start} to {period.end}. Reasons: {', '.join(s['reasons'])}."),
        ))
    session.commit()
    return len(leaks)
