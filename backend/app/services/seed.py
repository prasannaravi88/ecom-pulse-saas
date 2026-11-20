"""Deterministic, realistic 6-month D2C seed dataset.

Simulates customer acquisition driven by Meta/Google spend, repeat purchases
with SKU-dependent loyalty, a festive demand spike (Diwali for INR, BFCM for
USD) with pricier CPMs and deal-seeking cohorts that retain worse, prepaid vs
COD payment mix with COD-heavy RTO, SKU-specific refund rates, and campaign-
level ad spend with inflated platform-reported revenue.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
from sqlalchemy import insert
from sqlalchemy.orm import Session

from app.db.models import DailyAdSpend, Order, OrderItem, Product, Tenant
from app.db.session import analyze


@dataclass(frozen=True)
class SkuSpec:
    sku: str
    name: str
    category: str
    price: float
    cogs: float
    popularity: float  # relative basket pick weight
    loyalty: float  # multiplies repeat-purchase probability
    refund_rate: float  # P(line refunded | delivered)
    rto_mult: float  # multiplies order RTO probability
    ad_weight: float  # share of prospecting budget pointed at this SKU
    festive_boost: float = 1.0


@dataclass(frozen=True)
class TenantProfile:
    tenant_id: str
    name: str
    currency: str
    skus: list[SkuSpec]
    base_new_customers: float  # per day, pre-seasonality
    target_cac: float
    cod_share_new: float
    cod_share_repeat: float
    rto_prepaid: float
    rto_cod: float
    ship_first_unit: float
    ship_extra_unit: float
    return_pickup: float  # reverse logistics cost on refunded orders
    gateway_pct: float
    gateway_fixed: float
    cod_fee: float
    cpm_meta: float
    cpm_google: float
    meta_share: float = 0.64
    two_unit_share: float = 0.09  # P(line quantity == 2)
    festive_dates: dict[int, date] = field(default_factory=dict)
    seed: int = 7


INR_SKUS = [
    SkuSpec("GLW-VC-30", "Vitamin C Glow Serum 30ml", "Serums", 799, 165, 1.4, 1.05, 0.040, 1.0, 1.2),
    SkuSpec("HYD-CER-50", "Ceramide Daily Moisturiser 50g", "Moisturisers", 549, 105, 1.3, 1.75, 0.018, 0.8, 0.7),
    SkuSpec("SUN-SPF50", "SPF 50 Mineral Sunscreen", "Sun Care", 649, 135, 1.5, 1.85, 0.020, 0.8, 0.8),
    SkuSpec("CLN-GEL-100", "Gentle Gel Face Wash 100ml", "Cleansers", 349, 58, 1.2, 1.9, 0.015, 0.7, 0.35),
    SkuSpec("NGT-RET-30", "Retinol Night Serum 30ml", "Serums", 999, 225, 0.8, 1.15, 0.050, 1.0, 0.9),
    SkuSpec("HAIR-ONN-100", "Onion Hair Growth Oil 100ml", "Hair Care", 449, 92, 1.0, 1.25, 0.060, 1.2, 0.8),
    SkuSpec("DEV-LED-COMB", "LED Laser Hair Growth Comb", "Devices", 2499, 1180, 0.32, 0.20, 0.180, 1.9, 3.6),
    SkuSpec("KIT-FEST-GLOW", "Festive Glow Gift Box", "Gift Sets", 1899, 610, 0.18, 0.35, 0.050, 1.4, 1.0, 6.0),
    SkuSpec("WEL-SLIM-TEA", "Detox Slim Green Tea (30 bags)", "Wellness", 599, 150, 0.6, 0.55, 0.110, 2.1, 2.6),
    SkuSpec("LIP-TINT-DUO", "Tinted Lip Balm Duo", "Lip Care", 299, 68, 0.9, 1.05, 0.030, 0.9, 0.3),
    SkuSpec("BDY-SHEA-200", "Shea Butter Body Lotion 200ml", "Body Care", 449, 102, 0.7, 1.10, 0.020, 0.9, 0.3),
    SkuSpec("ACN-PATCH-36", "Hydrocolloid Acne Patches (36)", "Treatments", 249, 38, 0.8, 1.45, 0.020, 0.8, 0.25),
]

USD_SKUS = [
    SkuSpec("CB-HOUSE-12", "House Blend Whole Bean 12oz", "Coffee", 18, 5.4, 1.6, 2.0, 0.010, 1.0, 0.8),
    SkuSpec("CB-DECAF-12", "Swiss Water Decaf 12oz", "Coffee", 19, 6.1, 0.7, 1.7, 0.012, 1.0, 0.4),
    SkuSpec("CB-SINGLE-ETH", "Ethiopia Yirgacheffe 12oz", "Coffee", 24, 8.2, 0.9, 1.4, 0.015, 1.0, 0.7),
    SkuSpec("CB-COLD-PACK", "Cold Brew Pitcher Packs (6)", "Coffee", 22, 6.9, 0.8, 1.3, 0.020, 1.0, 0.6),
    SkuSpec("EQ-POUR-KIT", "Pour-Over Starter Kit", "Equipment", 64, 27, 0.35, 0.3, 0.060, 1.0, 1.8),
    SkuSpec("EQ-GRINDER", "Burr Grinder Pro", "Equipment", 149, 82, 0.2, 0.15, 0.140, 1.0, 2.8),
    SkuSpec("CB-ESPRESSO", "Espresso Roast 12oz", "Coffee", 19, 5.8, 1.0, 1.8, 0.010, 1.0, 0.5),
    SkuSpec("AC-MUG-CER", "Ceramic Logo Mug", "Accessories", 16, 4.5, 0.4, 0.4, 0.040, 1.0, 0.2, 2.5),
    SkuSpec("AC-TUMBLER", "Insulated Travel Tumbler", "Accessories", 32, 11, 0.35, 0.3, 0.050, 1.0, 0.6, 2.5),
    SkuSpec("CB-SAMPLER", "Origin Sampler (4 x 4oz)", "Coffee", 29, 9.5, 0.6, 0.9, 0.020, 1.0, 1.0, 3.0),
    SkuSpec("CB-SUBS-GIFT", "3-Month Gift Subscription", "Gifts", 54, 19, 0.15, 0.2, 0.030, 1.0, 0.5, 5.0),
    SkuSpec("AC-FILTERS", "Paper Filters (100)", "Accessories", 7, 1.6, 0.6, 1.6, 0.005, 1.0, 0.05),
]

PROFILES = [
    TenantProfile(
        tenant_id="lumiere-botanicals", name="Lumière Botanicals", currency="INR", skus=INR_SKUS,
        base_new_customers=78, target_cac=500, cod_share_new=0.58, cod_share_repeat=0.38,
        rto_prepaid=0.025, rto_cod=0.21, ship_first_unit=62, ship_extra_unit=12,
        return_pickup=70, gateway_pct=0.02, gateway_fixed=0, cod_fee=35,
        cpm_meta=185, cpm_google=125,
        festive_dates={2024: date(2024, 11, 1), 2025: date(2025, 10, 21),
                       2026: date(2026, 11, 8), 2027: date(2027, 10, 29)},
        seed=11,
    ),
    TenantProfile(
        tenant_id="northbound-coffee", name="Northbound Coffee Co.", currency="USD", skus=USD_SKUS,
        base_new_customers=34, target_cac=23, cod_share_new=0.0, cod_share_repeat=0.0,
        rto_prepaid=0.008, rto_cod=0.0, ship_first_unit=5.2, ship_extra_unit=1.1,
        return_pickup=7.5, gateway_pct=0.029, gateway_fixed=0.30, cod_fee=0,
        cpm_meta=14.5, cpm_google=9.8, two_unit_share=0.35,
        # Black Friday (4th Friday of November)
        festive_dates={2024: date(2024, 11, 29), 2025: date(2025, 11, 28),
                       2026: date(2026, 11, 27), 2027: date(2027, 11, 26)},
        seed=23,
    ),
]

# Base probability that a customer orders again in month k after acquisition.
REPEAT_CURVE = [0.0, 0.20, 0.14, 0.12, 0.10, 0.09, 0.085]


def _festive_peak(profile: TenantProfile, start: date, end: date) -> date:
    for peak in sorted(profile.festive_dates.values()):
        if start + timedelta(days=21) <= peak <= end:
            return peak
    return end - timedelta(days=40)


def _season(day: date, peak: date) -> float:
    """Demand multiplier: weekday rhythm + festive build-up / spike + payday bump."""
    weekday = [1.0, 0.97, 0.96, 0.98, 1.04, 1.10, 1.07][day.weekday()]
    gap = (day - peak).days
    # Build-up starts ~4 weeks before the peak, falls off fast afterwards.
    sigma = 13 if gap < 0 else 6
    festive = 1.0 + 1.65 * math.exp(-(gap**2) / (2 * sigma**2))
    payday = 1.06 if day.day <= 5 else 1.0
    return weekday * festive * payday


def _festive_intensity(day: date, peak: date) -> float:
    gap = (day - peak).days
    return math.exp(-(gap**2) / (2 * (13 if gap < 0 else 6) ** 2))


def generate(profile: TenantProfile, end: date, days: int = 183) -> dict:
    """Return plain dict rows for products, orders (with nested items) and ad spend."""
    rng = np.random.default_rng(profile.seed)
    start = end - timedelta(days=days - 1)
    peak = _festive_peak(profile, start, end)
    skus = profile.skus
    idx_by_sku = {s.sku: i for i, s in enumerate(skus)}
    is_inr = profile.currency == "INR"
    money_round = 0 if is_inr else 2

    def pick_basket(festive: float, prefer: list[int] | None) -> list[tuple[int, int]]:
        weights = np.array([s.popularity * (1 + (s.festive_boost - 1) * festive) for s in skus])
        if prefer:
            weights = weights.copy()
            for i in prefer:
                weights[i] *= 1 + 3 * skus[i].loyalty
        weights /= weights.sum()
        n_lines = int(rng.choice([1, 2, 3], p=[0.68, 0.25, 0.07]))
        chosen = rng.choice(len(skus), size=n_lines, replace=False, p=weights)
        return [(int(i), 2 if rng.random() < profile.two_unit_share else 1) for i in chosen]

    orders: list[dict] = []
    seq = 0
    # Per-day attributed gross by channel, used to build platform-reported revenue.
    platform_gross: dict[tuple[date, str], float] = {}
    new_by_day: dict[date, float] = {}

    def make_order(day: date, customer: str, is_new: bool, prefer: list[int] | None) -> list[int]:
        nonlocal seq
        festive = _festive_intensity(day, peak)
        basket = pick_basket(festive, prefer)
        if is_new:
            channel = rng.choice(["meta", "google", "organic"], p=[0.52, 0.30, 0.18])
        else:
            channel = rng.choice(["organic", "meta", "google"], p=[0.66, 0.21, 0.13])
        cod_p = profile.cod_share_new if is_new else profile.cod_share_repeat
        payment = "cod" if rng.random() < cod_p else "prepaid"

        coupon_p = 0.42 + 0.40 * festive
        disc_pct = 0.0
        if rng.random() < coupon_p:
            disc_pct = 0.10 + 0.12 * festive + (0.05 if rng.random() < 0.3 else 0.0)
        if payment == "prepaid" and is_inr:
            disc_pct += 0.05  # prepaid incentive
        if is_new:
            disc_pct += 0.05  # welcome offer

        rto_mult = max(skus[i].rto_mult for i, _ in basket)
        rto_p = (profile.rto_cod if payment == "cod" else profile.rto_prepaid) * rto_mult
        rto_p *= 0.55 if not is_new else 1.0
        rto_p *= 1 + 0.25 * festive
        roll = rng.random()
        status = "cancelled" if roll < 0.018 else ("rto" if roll < 0.018 + min(rto_p, 0.6) else "delivered")

        items, units, gross_total, net_total = [], 0, 0.0, 0.0
        for i, qty in basket:
            spec = skus[i]
            gross = spec.price * qty
            discount = round(gross * disc_pct, money_round)
            refund = 0.0
            if status == "delivered" and rng.random() < spec.refund_rate:
                refund = round(gross - discount, 2)
            items.append({"sku": spec.sku, "quantity": qty, "unit_price": spec.price,
                          "discount": discount, "refund_amount": refund, "unit_cogs": spec.cogs})
            units += qty
            gross_total += gross
            net_total += gross - discount
        if status == "delivered" and any(it["refund_amount"] > 0 for it in items):
            status = "refunded"

        shipping = 0.0
        if status != "cancelled":
            shipping = profile.ship_first_unit + profile.ship_extra_unit * (units - 1)
            if status == "rto":
                shipping *= 2  # forward + return-to-origin leg
            elif status == "refunded":
                shipping += profile.return_pickup
        fee = 0.0
        if status in ("delivered", "refunded"):
            fee = profile.cod_fee if payment == "cod" else net_total * profile.gateway_pct + profile.gateway_fixed
        elif status == "rto" and payment == "prepaid":
            fee = net_total * profile.gateway_pct + profile.gateway_fixed  # gateway keeps its cut

        seq += 1
        prefix = "LB" if is_inr else "NB"
        orders.append({
            "order_ref": f"{prefix}-{100000 + seq}",
            "order_date": day,
            "customer_ref": customer,
            "status": status,
            "payment_method": payment,
            "attributed_channel": str(channel),
            "shipping_cost": round(shipping, 2),
            "payment_fee": round(fee, 2),
            "items": items,
        })
        if status != "cancelled" and channel != "organic":
            key = (day, str(channel))
            platform_gross[key] = platform_gross.get(key, 0.0) + gross_total
        return [i for i, _ in basket]

    repeat_schedule: dict[date, list[tuple[str, list[int]]]] = {}
    cust_seq = 0
    for offset in range(days):
        day = start + timedelta(days=offset)
        growth = 1 + 0.035 * (offset / 30)
        expected_new = profile.base_new_customers * _season(day, peak) * growth
        new_by_day[day] = expected_new
        festive = _festive_intensity(day, peak)
        for _ in range(int(rng.poisson(expected_new))):
            cust_seq += 1
            customer = f"{profile.tenant_id[:2].upper()}C{cust_seq:06d}"
            bought = make_order(day, customer, True, None)
            loyalty = float(np.mean([skus[i].loyalty for i in bought]))
            # Festive deal-seekers retain worse.
            loyalty *= 1 - 0.35 * festive
            for k in range(1, len(REPEAT_CURVE)):
                if rng.random() < REPEAT_CURVE[k] * loyalty:
                    when = day + timedelta(days=int(30 * k + rng.integers(-12, 13)))
                    if when <= end:
                        repeat_schedule.setdefault(when, []).append((customer, bought))
        for customer, prefer in repeat_schedule.pop(day, []):
            make_order(day, customer, False, prefer)

    # ---- ad spend: budget follows expected acquisition; CPMs and CAC inflate in festive weeks.
    meta_sku_w = {s.sku: s.ad_weight for s in skus}
    google_shopping = sorted(skus, key=lambda s: -s.ad_weight)[:4]
    spend_rows: list[dict] = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        festive = _festive_intensity(day, peak)
        cac_mult = 1 + 0.22 * festive
        paid_new = new_by_day[day] * 0.82
        total = paid_new * profile.target_cac * cac_mult * float(rng.normal(1, 0.08))
        for channel, share, cpm, inflation in (
            ("meta", profile.meta_share, profile.cpm_meta, 1.25),
            ("google", 1 - profile.meta_share, profile.cpm_google, 1.12),
        ):
            budget = total * share
            if channel == "meta":
                campaigns = [(f"Meta | Prospecting | {s}", s, 0.72 * w / sum(meta_sku_w.values()))
                             for s, w in meta_sku_w.items()]
                campaigns.append(("Meta | Retargeting | DPA All Products", None, 0.28))
            else:
                gw = sum(s.ad_weight for s in google_shopping)
                campaigns = [(f"Google | Shopping | {s.sku}", s.sku, 0.45 * s.ad_weight / gw)
                             for s in google_shopping]
                campaigns += [("Google | Search | Brand", None, 0.20),
                              ("Google | PMax | Catalogue", None, 0.35)]
            claimed = platform_gross.get((day, channel), 0.0) * inflation * float(rng.normal(1, 0.1))
            for name, sku, weight in campaigns:
                amount = round(budget * weight * float(rng.normal(1, 0.12)), 2)
                if amount <= 0:
                    continue
                impressions = int(amount / (cpm * (1 + 0.35 * festive)) * 1000)
                spend_rows.append({
                    "spend_date": day, "channel": channel, "campaign": name, "sku": sku,
                    "spend": amount, "impressions": impressions,
                    "clicks": int(impressions * float(rng.normal(0.012, 0.002))),
                    "platform_reported_revenue": round(claimed * weight, 2),
                })

    products = [{"sku": s.sku, "name": s.name, "category": s.category,
                 "price": s.price, "unit_cogs": s.cogs} for s in skus]
    return {"products": products, "orders": orders, "spend": spend_rows,
            "start": start, "end": end, "festive_peak": peak}


def write_dataset(session: Session, tenant_id: str, data: dict) -> None:
    """Bulk-insert a generated dataset for an existing tenant."""
    session.execute(insert(Product), [{"tenant_id": tenant_id, **p} for p in data["products"]])
    order_rows = [{k: v for k, v in o.items() if k != "items"} | {"tenant_id": tenant_id}
                  for o in data["orders"]]
    ids: dict[str, int] = {}
    for chunk_start in range(0, len(order_rows), 5000):
        chunk = order_rows[chunk_start:chunk_start + 5000]
        result = session.execute(insert(Order).returning(Order.id, Order.order_ref), chunk)
        ids.update({ref: oid for oid, ref in result.all()})
    item_rows = [{"tenant_id": tenant_id, "order_id": ids[o["order_ref"]], **it}
                 for o in data["orders"] for it in o["items"]]
    for chunk_start in range(0, len(item_rows), 10000):
        session.execute(insert(OrderItem), item_rows[chunk_start:chunk_start + 10000])
    for chunk_start in range(0, len(data["spend"]), 10000):
        session.execute(insert(DailyAdSpend),
                        [{"tenant_id": tenant_id, **r} for r in data["spend"][chunk_start:chunk_start + 10000]])


def seed_all(session: Session, end: date | None = None) -> list[str]:
    """Create demo tenants that don't exist yet. Returns the ids seeded."""
    end = end or date.today()
    seeded = []
    for profile in PROFILES:
        if session.get(Tenant, profile.tenant_id) is not None:
            continue
        session.add(Tenant(id=profile.tenant_id, name=profile.name, currency=profile.currency))
        session.flush()
        write_dataset(session, profile.tenant_id, generate(profile, end))
        seeded.append(profile.tenant_id)
    session.commit()
    if seeded:
        analyze(session)
    return seeded

