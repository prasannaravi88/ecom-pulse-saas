"""End-to-end API checks against the full seeded dataset."""

import math
import re

import pytest
from sqlalchemy import func, select

from app.db.models import Order
from app.db.session import SessionLocal
from app.services.seed import PROFILES
from tests.conftest import SEED_END

INR, USD = PROFILES[0].tenant_id, PROFILES[1].tenant_id


def _walk_numbers(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_numbers(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_numbers(v)
    elif isinstance(obj, float):
        yield obj


def _get(client, path, tenant=INR, **params):
    r = client.get(path, headers={"X-Tenant-ID": tenant}, params=params)
    assert r.status_code == 200, r.text
    body = r.json()
    assert all(math.isfinite(x) for x in _walk_numbers(body)), f"non-finite number in {path}"
    return body


def test_tenants(client):
    tenants = {t["id"]: t["currency"] for t in client.get("/api/tenants").json()}
    assert tenants == {INR: "INR", USD: "USD"}


def test_tenant_required(client):
    assert client.get("/api/metrics/overview").status_code == 400
    assert client.get("/api/metrics/overview", headers={"X-Tenant-ID": "nope"}).status_code == 404


def test_seed_shape():
    with SessionLocal() as s:
        for tid in (INR, USD):
            n = s.scalar(select(func.count()).select_from(Order).where(Order.tenant_id == tid))
            assert n > 5_000
            lo, hi = s.execute(select(func.min(Order.order_date), func.max(Order.order_date))
                               .where(Order.tenant_id == tid)).one()
            assert (hi - lo).days >= 175 and hi == SEED_END
        cod = s.scalar(select(func.count()).select_from(Order)
                       .where(Order.tenant_id == INR, Order.payment_method == "cod"))
        total = s.scalar(select(func.count()).select_from(Order).where(Order.tenant_id == INR))
        assert 0.4 < cod / total < 0.65


@pytest.mark.parametrize("rng", ["7D", "30D", "90D", "QTD"])
def test_overview_real_numbers(client, rng):
    body = _get(client, "/api/metrics/overview", range=rng)
    k = body["kpis"]
    assert body["currency"] == "INR" and body["period"]["end"] == SEED_END.isoformat()
    for key in ["net_revenue", "ad_spend", "cm2", "cm2_pct", "blended_mer", "blended_cac"]:
        assert k[key]["value"] is not None and k[key]["previous"] is not None
        assert k[key]["delta_pct"] is not None
    assert k["net_revenue"]["value"] > 0 and k["ad_spend"]["value"] > 0
    # Formula identities hold on the served numbers.
    assert k["net_revenue"]["value"] == pytest.approx(
        k["gross_revenue"]["value"] - k["discounts"]["value"] - k["refunds_rto"]["value"], abs=0.05)
    assert k["cm2"]["value"] == pytest.approx(
        k["cm1"]["value"] - k["shipping"]["value"] - k["payment_fees"]["value"] - k["ad_spend"]["value"], abs=0.05)
    assert k["blended_mer"]["value"] == pytest.approx(k["net_revenue"]["value"] / k["ad_spend"]["value"], abs=1e-3)


def test_realistic_d2c_ranges(client):
    k = _get(client, "/api/metrics/overview", range="90D")["kpis"]
    assert 0.08 < k["rto_rate"]["value"] < 0.25
    assert 0.02 < k["refund_rate"]["value"] < 0.10
    assert 1.5 < k["blended_mer"]["value"] < 4
    assert 0.55 < k["cm1_pct"]["value"] < 0.8


def test_festive_spike(client):
    days = _get(client, "/api/metrics/daily", range="90D")["days"]
    peak = PROFILES[0].festive_dates[SEED_END.year].isoformat()
    by_day = {d["date"]: d["net_revenue"] for d in days}
    from datetime import date, timedelta
    cutoff = (date.fromisoformat(peak) - timedelta(days=40)).isoformat()
    baseline = sorted(v for d, v in by_day.items() if d < cutoff)
    median = baseline[len(baseline) // 2]
    assert by_day[peak] > 1.8 * median


def test_skus_reconcile_with_overview(client):
    skus = _get(client, "/api/metrics/skus", range="30D")
    ov = _get(client, "/api/metrics/overview", range="30D")
    assert len(skus["skus"]) == 12
    assert sum(s["cm2"] for s in skus["skus"]) == pytest.approx(ov["kpis"]["cm2"]["value"], abs=1)
    segments = {s["sku"]: s["segment"] for s in skus["skus"]}
    assert segments["DEV-LED-COMB"] == "margin_leak" and segments["WEL-SLIM-TEA"] == "margin_leak"
    assert "hero" in segments.values()


def test_channels_true_roas_below_platform(client):
    ch = {c["channel"]: c for c in _get(client, "/api/metrics/channels", range="30D")["channels"]}
    for name in ("meta", "google"):
        assert ch[name]["true_roas"] < ch[name]["platform_roas"]
        assert ch[name]["roas_inflation"] > 1.2


def test_cohorts(client):
    body = _get(client, "/api/cohorts")
    rows = body["cohorts"]
    assert len(rows) == 7  # May (partial) .. Nov
    first = rows[0]
    assert [c["available"] for c in first["cells"]] == [True] * 7
    assert first["cells"][0]["retention"] == 1.0
    assert 0.1 < first["cells"][1]["retention"] < 0.35
    assert rows[-1]["cells"][1]["available"] is False
    # LTV is cumulative, so it never decreases month to month.
    for row in rows:
        ltv = [c["ltv_net_revenue"] for c in row["cells"] if c["available"]]
        assert ltv == sorted(ltv)


def test_currency_isolation(client):
    inr = _get(client, "/api/metrics/overview", tenant=INR, range="30D")
    usd = _get(client, "/api/metrics/overview", tenant=USD, range="30D")
    assert usd["currency"] == "USD"
    assert usd["kpis"]["aov"]["value"] < 100 < inr["kpis"]["aov"]["value"]
    assert {s["sku"] for s in _get(client, "/api/metrics/skus", tenant=USD)["skus"]}.isdisjoint(
        {s["sku"] for s in _get(client, "/api/metrics/skus", tenant=INR)["skus"]})


def test_upload_recomputes_live(client):
    before = _get(client, "/api/metrics/overview", tenant=USD, range="7D")["kpis"]
    csv = ("order_id,order_date,customer_id,sku,quantity,unit_price\n"
           f"UP-1,{SEED_END},api-test-cust,CB-HOUSE-12,10,18\n")
    r = client.post("/api/ingest/orders", headers={"X-Tenant-ID": USD},
                    files={"file": ("orders.csv", csv, "text/csv")})
    assert r.status_code == 200, r.text
    after = _get(client, "/api/metrics/overview", tenant=USD, range="7D")["kpis"]
    assert after["net_revenue"]["value"] == pytest.approx(before["net_revenue"]["value"] + 180, abs=0.01)
    assert after["orders"]["value"] == before["orders"]["value"] + 1
    inr_untouched = _get(client, "/api/metrics/overview", tenant=INR, range="7D")["kpis"]
    assert inr_untouched["orders"]["value"] > 0

    spend = f"date,channel,campaign,spend\n{SEED_END},meta,API Test,500\n"
    r = client.post("/api/ingest/ad-spend", headers={"X-Tenant-ID": USD},
                    files={"file": ("spend.csv", spend, "text/csv")})
    assert r.status_code == 200, r.text
    final = _get(client, "/api/metrics/overview", tenant=USD, range="7D")["kpis"]
    assert final["ad_spend"]["value"] == pytest.approx(after["ad_spend"]["value"] + 500, abs=0.01)


def test_upload_validation_error_shape(client):
    r = client.post("/api/ingest/orders", headers={"X-Tenant-ID": INR},
                    files={"file": ("orders.csv", "order_id\nX\n", "text/csv")})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert {e["column"] for e in detail["errors"]} >= {"order_date", "sku"}
    r = client.post("/api/ingest/orders", headers={"X-Tenant-ID": INR},
                    files={"file": ("orders.xlsx", b"x", "application/octet-stream")})
    assert r.status_code == 415


def test_templates_ingest_cleanly(client):
    tpl = client.get("/api/ingest/templates/orders").text
    r = client.post("/api/ingest/orders", headers={"X-Tenant-ID": INR},
                    files={"file": ("t.csv", tpl, "text/csv")})
    assert r.status_code == 200, r.text
    tpl = client.get("/api/ingest/templates/ad-spend").text
    r = client.post("/api/ingest/ad-spend", headers={"X-Tenant-ID": INR},
                    files={"file": ("t.csv", tpl, "text/csv")})
    assert r.status_code == 200, r.text


def test_alerts_generated(client):
    r = client.get("/api/alerts", headers={"X-Tenant-ID": INR})
    assert r.status_code == 200
    assert any(a["subject"] == "DEV-LED-COMB" for a in r.json())


def _ask(client, tenant=INR, **body):
    r = client.post("/api/analyst/ask", headers={"X-Tenant-ID": tenant}, json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    assert all(math.isfinite(x) for x in _walk_numbers(data))
    return data


@pytest.mark.parametrize("prompt_id", [p["id"] for p in __import__("app.services.analyst", fromlist=["x"]).prompts()])
@pytest.mark.parametrize("tenant", [INR, USD])
def test_analyst_prompts_answer(client, prompt_id, tenant):
    a = _ask(client, tenant=tenant, prompt_id=prompt_id, range="30D")
    assert a["status"] == "answered" and a["intent"] == prompt_id
    assert a["summary"] and all(isinstance(s, str) and not re.search(r"\b(None|nan|n/a)\b", s) for s in a["summary"])
    assert a["guardrail"].startswith("Read-only")


def test_analyst_numbers_match_dashboard(client):
    a = _ask(client, prompt_id="margin_leaks", range="30D")
    skus = {s["sku"]: s for s in _get(client, "/api/metrics/skus", range="30D")["skus"]}
    for row in a["table"]["rows"]:
        assert row["cm2"] == skus[row["sku"]]["cm2"] and skus[row["sku"]]["segment"] == "margin_leak"
    bridge = _ask(client, prompt_id="cm2_bridge", range="30D")["table"]["rows"]
    assert sum(r["impact"] for r in bridge[:-1]) == pytest.approx(bridge[-1]["impact"], abs=0.05)


def test_analyst_display_currency(client):
    inr = _ask(client, prompt_id="cod_rto", range="30D")
    usd = _ask(client, prompt_id="cod_rto", range="30D", display_currency="USD")
    assert "₹" in inr["title"] and "$" in usd["title"]
    # Table money stays in the tenant's currency; only narrative is converted.
    assert inr["table"]["rows"] == usd["table"]["rows"]


def test_analyst_guardrails(client):
    assert _ask(client, question="DROP TABLE orders")["status"] == "refused"
    assert _ask(client, question="delete the margin leak SKUs")["status"] == "refused"
    off = _ask(client, question="what's the weather like")
    assert off["status"] == "unsupported" and len(off["suggestions"]) == 6
    free = _ask(client, question="facebook vs google after refunds?")
    assert free["status"] == "answered" and free["intent"] == "channel_roas"


def test_payment_mix_reconciles(client):
    mix = _get(client, "/api/metrics/payment-mix", range="30D")["payment_mix"]
    ov = _get(client, "/api/metrics/overview", range="30D")["kpis"]
    assert sum(m["orders"] for m in mix) == ov["orders"]["value"]
    assert sum(m["net_revenue"] for m in mix) == pytest.approx(ov["net_revenue"]["value"], abs=0.1)
    cod = next(m for m in mix if m["payment_method"] == "cod")
    assert cod["rto_rate"] > next(m for m in mix if m["payment_method"] == "prepaid")["rto_rate"] * 3


def test_fx(client):
    assert client.get("/api/fx").json()["rate"] > 0
