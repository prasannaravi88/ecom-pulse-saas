"""DuckDB rollups checked against a small hand-worked ledger.

Ledger for tenant "acme" (all values hand-computed in the comments):

  O1 2026-01-05  A  delivered prepaid meta    ship 50  fee 20
       X 2 x 100, disc 20, cogs 30/u     Y 1 x 200, cogs 80
       gross 400, disc 20, returns 0, net 380, cogs 140
  O2 2026-01-10  B  rto       cod     google  ship 100 fee 0
       X 1 x 100, disc 10                 -> returns 90 (full line), cogs 0
  O3 2026-01-20  C  refunded  prepaid meta    ship 60  fee 5
       Y 1 x 200, refund 200, cogs 80     X 1 x 100, cogs 30
       gross 300, returns 200, net 100, cogs 110 (refunded goods written off)
  O4 2026-01-25  D  cancelled                 -> ignored everywhere
  O5 2026-02-03  A  delivered organic  X 1 x 100       ship 40 fee 10
  O7 2026-02-10  E  delivered meta     X 1 x 100       ship 40 fee 5
  O8 2026-02-20  C  rto       organic  X 1 x 100       ship 80
  O6 2026-03-15  A  delivered organic  Y 1 x 200       ship 40 fee 10

  Spend: Jan meta 300 (campaign mapped to SKU X, platform claims 1200)
         Jan google 100 (brand, unmapped, platform claims 300)
         Feb meta 150 (unmapped)

January: gross 800, discounts 30, refunds/RTO 290, net 480, COGS 250, CM1 230,
shipping 210, fees 25, ads 400, CM2 -405, MER 1.2, new customers 3 (A,B,C).
"""

from datetime import date

import pytest

from app.db.models import DailyAdSpend, Order, OrderItem
from app.services.analytics import AnalyticsEngine, resolve_period

JAN = (date(2026, 1, 1), date(2026, 1, 31))


def _order(s, tenant, ref, day, cust, status, pay, channel, ship, fee, items):
    o = Order(tenant_id=tenant, order_ref=ref, order_date=day, customer_ref=cust, status=status,
              payment_method=pay, attributed_channel=channel, shipping_cost=ship, payment_fee=fee)
    for sku, qty, price, disc, refund, cogs in items:
        o.items.append(OrderItem(tenant_id=tenant, sku=sku, quantity=qty, unit_price=price,
                                 discount=disc, refund_amount=refund, unit_cogs=cogs))
    s.add(o)


@pytest.fixture
def engine(session):
    a = "acme"
    _order(session, a, "O1", date(2026, 1, 5), "A", "delivered", "prepaid", "meta", 50, 20,
           [("X", 2, 100, 20, 0, 30), ("Y", 1, 200, 0, 0, 80)])
    _order(session, a, "O2", date(2026, 1, 10), "B", "rto", "cod", "google", 100, 0,
           [("X", 1, 100, 10, 0, 30)])
    _order(session, a, "O3", date(2026, 1, 20), "C", "refunded", "prepaid", "meta", 60, 5,
           [("Y", 1, 200, 0, 200, 80), ("X", 1, 100, 0, 0, 30)])
    _order(session, a, "O4", date(2026, 1, 25), "D", "cancelled", "prepaid", "meta", 0, 0,
           [("X", 5, 100, 0, 0, 30)])
    _order(session, a, "O5", date(2026, 2, 3), "A", "delivered", "prepaid", "organic", 40, 10,
           [("X", 1, 100, 0, 0, 30)])
    _order(session, a, "O7", date(2026, 2, 10), "E", "delivered", "prepaid", "meta", 40, 5,
           [("X", 1, 100, 0, 0, 30)])
    _order(session, a, "O8", date(2026, 2, 20), "C", "rto", "cod", "organic", 80, 0,
           [("X", 1, 100, 0, 0, 30)])
    _order(session, a, "O6", date(2026, 3, 15), "A", "delivered", "prepaid", "organic", 40, 10,
           [("Y", 1, 200, 0, 0, 80)])
    session.add_all([
        DailyAdSpend(tenant_id=a, spend_date=date(2026, 1, 5), channel="meta", campaign="P-X",
                     sku="X", spend=300, platform_reported_revenue=1200),
        DailyAdSpend(tenant_id=a, spend_date=date(2026, 1, 6), channel="google", campaign="Brand",
                     sku=None, spend=100, platform_reported_revenue=300),
        DailyAdSpend(tenant_id=a, spend_date=date(2026, 2, 1), channel="meta", campaign="Retarget",
                     sku=None, spend=150, platform_reported_revenue=500),
    ])
    # Noise in another tenant that must never leak into acme's numbers.
    _order(session, "other", "O1", date(2026, 1, 5), "A", "delivered", "prepaid", "meta", 999, 999,
           [("X", 50, 1000, 0, 0, 1)])
    session.add(DailyAdSpend(tenant_id="other", spend_date=date(2026, 1, 5), channel="meta",
                             campaign="P-X", sku="X", spend=99_999))
    session.commit()
    eng = AnalyticsEngine.for_tenant(session, "acme")
    yield eng
    eng.close()


def test_january_pnl(engine):
    p = engine.pnl(*JAN)
    assert p["gross_revenue"] == pytest.approx(800)
    assert p["discounts"] == pytest.approx(30)
    assert p["refunds_rto"] == pytest.approx(290)
    assert p["net_revenue"] == pytest.approx(480)
    assert p["cogs"] == pytest.approx(250)
    assert p["cm1"] == pytest.approx(230)
    assert p["shipping"] == pytest.approx(210)
    assert p["payment_fees"] == pytest.approx(25)
    assert p["ad_spend"] == pytest.approx(400)
    assert p["cm2"] == pytest.approx(-405)
    assert p["cm2_pct"] == pytest.approx(-405 / 480)
    assert p["blended_mer"] == pytest.approx(1.2)
    assert p["new_customers"] == 3  # D only had a cancelled order
    assert p["blended_cac"] == pytest.approx(400 / 3)
    assert p["orders"] == 3
    assert p["rto_rate"] == pytest.approx(1 / 3)
    assert p["refund_rate"] == pytest.approx(1 / 3)
    assert p["cod_share"] == pytest.approx(1 / 3)


def test_tenant_isolation(engine, session):
    other = AnalyticsEngine.for_tenant(session, "other")
    try:
        assert other.pnl(*JAN)["gross_revenue"] == pytest.approx(50_000)
        assert other.pnl(*JAN)["ad_spend"] == pytest.approx(99_999)
    finally:
        other.close()
    assert engine.pnl(*JAN)["ad_spend"] == pytest.approx(400)


def test_daily_rows_sum_to_period_pnl(engine):
    days = engine.daily(*JAN)
    assert len(days) == 31
    p = engine.pnl(*JAN)
    for key, pnl_key in [("net_revenue", "net_revenue"), ("cm1", "cm1"), ("cm2", "cm2"),
                         ("ad_spend", "ad_spend"), ("refunds_rto", "refunds_rto")]:
        assert sum(d[key] for d in days) == pytest.approx(p[pnl_key])
    jan5 = next(d for d in days if d["date"] == "2026-01-05")
    assert jan5["net_revenue"] == pytest.approx(380)
    assert jan5["meta_spend"] == pytest.approx(300)
    assert jan5["mer"] == pytest.approx(380 / 300, abs=1e-4)
    empty_day = next(d for d in days if d["date"] == "2026-01-02")
    assert empty_day["net_revenue"] == 0 and empty_day["mer"] is None


def test_waterfall_reconciles(engine):
    from app.services.analytics import custom_period

    wf = engine.overview(custom_period(*JAN))["waterfall"]
    steps = {s["step"]: s["value"] for s in wf}
    assert steps["Gross Sales"] == 800
    assert steps["Net Revenue"] == 480
    assert steps["CM1"] == 230
    assert steps["Net Contribution (CM2)"] == -405
    decreases = sum(s["value"] for s in wf if s["kind"] == "decrease")
    assert steps["Gross Sales"] + decreases == pytest.approx(steps["Net Contribution (CM2)"])


def test_channels_true_vs_platform_roas(engine):
    ch = {c["channel"]: c for c in engine.channels(*JAN)}
    meta, google = ch["meta"], ch["google"]
    assert meta["ad_spend"] == 300
    assert meta["attributed_net_revenue"] == 480  # O1 380 + O3 100
    assert meta["true_roas"] == pytest.approx(1.6)
    assert meta["platform_roas"] == pytest.approx(4.0)
    assert meta["roas_inflation"] == pytest.approx(2.5)
    assert meta["new_customers"] == 2 and meta["cac"] == pytest.approx(150)
    assert google["attributed_net_revenue"] == 0  # its only order was RTO'd
    assert google["true_roas"] == 0
    assert google["platform_roas"] == pytest.approx(3.0)
    assert google["cac"] == pytest.approx(100)


def test_cohort_matrix(engine):
    data = engine.cohorts()
    assert data["as_of"] == "2026-03-15"
    rows = {r["cohort_month"]: r for r in data["cohorts"]}
    assert set(rows) == {"2026-01-01", "2026-02-01"}

    jan = rows["2026-01-01"]
    assert jan["size"] == 3
    assert jan["cac"] == pytest.approx(133.33, abs=0.01)
    m0, m1, m2, m3 = jan["cells"][:4]
    assert (m0["active"], m0["retention"]) == (3, 1.0)
    # Contribution: O1 170, O2 -100, O3 -75 -> -5 over 3 customers
    assert m0["ltv_contribution"] == pytest.approx(-1.67, abs=0.01)
    assert m0["ltv_net_revenue"] == pytest.approx(160)
    # Feb: A repeats (O5); C's order was RTO so C is not active.
    assert (m1["active"], m1["retention"]) == (1, pytest.approx(0.3333, abs=1e-4))
    assert m1["ltv_contribution"] == pytest.approx(-65 / 3, abs=0.01)  # O5 +20, O8 -80
    assert m1["ltv_net_revenue"] == pytest.approx(580 / 3, abs=0.01)
    assert m2["active"] == 1 and m2["partial"] is True
    assert m2["ltv_contribution"] == pytest.approx(5 / 3, abs=0.01)  # O6 +70
    assert m3["available"] is False and m3["retention"] is None
    assert jan["payback_month"] is None

    feb = rows["2026-02-01"]
    assert feb["size"] == 1 and feb["cac"] == pytest.approx(150)
    assert feb["cells"][0]["ltv_contribution"] == pytest.approx(25)
    assert feb["cells"][1]["active"] == 0 and feb["cells"][1]["retention"] == 0.0


def test_cohort_payback_detected(session, engine):
    # A big first order from Z makes the Feb cohort (E + Z) pay back CAC in M0.
    _order(session, "acme", "Z1", date(2026, 2, 5), "Z", "delivered", "prepaid", "organic", 0, 0,
           [("Y", 5, 200, 0, 0, 80)])
    session.commit()
    eng = AnalyticsEngine.for_tenant(session, "acme")
    try:
        feb = next(r for r in eng.cohorts()["cohorts"] if r["cohort_month"] == "2026-02-01")
        assert feb["size"] == 2 and feb["cac"] == pytest.approx(75)
        assert feb["cells"][0]["ltv_contribution"] == pytest.approx((25 + 600) / 2)
        assert feb["payback_month"] == 0
        assert feb["ltv_to_cac"] == pytest.approx(312.5 / 75, abs=1e-3)
    finally:
        eng.close()


def test_sku_rollup_and_allocation(engine):
    rows = {r["sku"]: r for r in engine.skus(*JAN)["skus"]}
    x, y = rows["X"], rows["Y"]
    assert x["gross_revenue"] == 400 and x["discounts"] == 30 and x["refunds_rto"] == 90
    assert x["net_revenue"] == 280 and x["cogs"] == 90 and x["cm1"] == 190
    # Order-level shipping/fees allocated by line gross share.
    assert x["shipping_fees"] == pytest.approx(145 + 11.67, abs=0.01)
    # 300 direct + 100 unmapped spend pro-rata by net revenue (280 / 480).
    assert x["ad_spend"] == pytest.approx(300 + 100 * 280 / 480, abs=0.01)
    assert x["cm2"] == pytest.approx(-325, abs=0.01)
    assert y["net_revenue"] == 200 and y["cogs"] == 160
    assert y["ad_spend"] == pytest.approx(41.67, abs=0.01)
    assert y["cm2"] == pytest.approx(-80, abs=0.01)
    assert y["return_rate"] == pytest.approx(0.5)
    # SKU CM2 must reconcile exactly to the tenant P&L.
    assert x["cm2"] + y["cm2"] == pytest.approx(engine.pnl(*JAN)["cm2"], abs=0.02)
    assert x["segment"] == y["segment"] == "margin_leak"
    assert "negative CM2" in x["reasons"]
    assert x["name"] == "Serum X" and y["name"] == "Y"  # Y has no catalogue row


def test_sku_repeat_rate(engine):
    rows = {r["sku"]: r for r in engine.skus(date(2026, 1, 1), date(2026, 3, 31))["skus"]}
    # X buyers (non-RTO): A (O1, O5), C (O3), E (O7) -> A repeats -> 1/3
    assert rows["X"]["repeat_rate"] == pytest.approx(1 / 3, abs=1e-4)
    # Y buyers: A (O1, O6), C (O3) -> 1/2
    assert rows["Y"]["repeat_rate"] == pytest.approx(0.5)


def test_overview_deltas(engine):
    from app.services.analytics import Period

    period = Period(date(2026, 2, 1), date(2026, 2, 28), date(2026, 1, 1), date(2026, 1, 28))
    k = engine.overview(period)["kpis"]
    # Feb: O5 100 + O7 100 + O8 RTO 0 = 200 net; Jan 1-28 = 480
    assert k["net_revenue"]["value"] == 200
    assert k["net_revenue"]["previous"] == 480
    assert k["net_revenue"]["delta_pct"] == pytest.approx((200 - 480) / 480, abs=1e-4)
    assert k["ad_spend"]["value"] == 150
    assert k["blended_cac"]["value"] == 150  # only E is new in Feb


def test_empty_tenant_returns_zeros_not_nan(session):
    eng = AnalyticsEngine.for_tenant(session, "acme")  # no orders yet
    try:
        start, end = date(2026, 1, 1), date(2026, 1, 31)
        p = eng.pnl(start, end)
        assert p["net_revenue"] == 0 and p["blended_mer"] is None and p["cm2_pct"] is None
        assert eng.skus(start, end)["skus"] == []
        assert eng.cohorts()["cohorts"] == []
        assert eng.channels(start, end) == []
        assert all(d["cm2"] == 0 for d in eng.daily(start, end))
    finally:
        eng.close()


@pytest.mark.parametrize("preset,as_of,expected", [
    ("7D", date(2026, 11, 20), (date(2026, 11, 14), date(2026, 11, 20), date(2026, 11, 7), date(2026, 11, 13))),
    ("30D", date(2026, 11, 20), (date(2026, 10, 22), date(2026, 11, 20), date(2026, 9, 22), date(2026, 10, 21))),
    ("90D", date(2026, 3, 31), (date(2026, 1, 1), date(2026, 3, 31), date(2025, 10, 3), date(2025, 12, 31))),
    ("QTD", date(2026, 11, 20), (date(2026, 10, 1), date(2026, 11, 20), date(2026, 7, 1), date(2026, 8, 20))),
    ("QTD", date(2026, 5, 31), (date(2026, 4, 1), date(2026, 5, 31), date(2026, 1, 1), date(2026, 3, 2))),
    # Q3 (92 days) is longer than Q2 (91): the comparison window is clamped to Q2's last day.
    ("QTD", date(2026, 9, 30), (date(2026, 7, 1), date(2026, 9, 30), date(2026, 4, 1), date(2026, 6, 30))),
    ("QTD", date(2026, 1, 15), (date(2026, 1, 1), date(2026, 1, 15), date(2025, 10, 1), date(2025, 10, 15))),
])
def test_resolve_period(preset, as_of, expected):
    p = resolve_period(preset, as_of)
    assert (p.start, p.end, p.prev_start, p.prev_end) == expected
