"""Canonical financial formulas (CLAUDE.md §1). Single source of truth.

The DuckDB rollups in `analytics.py` implement the same definitions in SQL; the
unit tests assert both agree. Ratios return None (not NaN/inf) when the
denominator is zero so the API never emits NaN.
"""

from __future__ import annotations


def safe_div(numerator: float, denominator: float) -> float | None:
    if denominator is None or denominator == 0:
        return None
    return numerator / denominator


def net_revenue(gross_revenue: float, discounts: float, refunds_rto: float) -> float:
    """Net Revenue = Gross Revenue - Discounts - Refunds/RTO."""
    return gross_revenue - discounts - refunds_rto


def cm1(net_rev: float, cogs: float) -> float:
    """CM1 (Gross Margin) = Net Revenue - COGS."""
    return net_rev - cogs


def cm2(cm1_value: float, shipping: float, payment_fees: float, ad_spend: float) -> float:
    """CM2 (Contribution Margin) = CM1 - Shipping & Payment Gateway Fees - Ad Spend."""
    return cm1_value - shipping - payment_fees - ad_spend


def blended_mer(net_rev: float, total_ad_spend: float) -> float | None:
    """Blended MER = Net Revenue / Total Ad Spend."""
    return safe_div(net_rev, total_ad_spend)


def true_roas(attributed_net_revenue: float, attributed_ad_spend: float) -> float | None:
    """True ROAS = Net Revenue (after discounts, refunds, RTO) / Attributed Ad Spend."""
    return safe_div(attributed_net_revenue, attributed_ad_spend)


def platform_roas(platform_reported_revenue: float, ad_spend: float) -> float | None:
    """What Meta/Google claim. Shown only as a contrast to True ROAS."""
    return safe_div(platform_reported_revenue, ad_spend)


def blended_cac(total_ad_spend: float, new_customers: int) -> float | None:
    return safe_div(total_ad_spend, new_customers)


def cohort_retention_rate(active_repeat_customers: int, cohort_size: int) -> float | None:
    """Retention (Month N) = Active Repeat Customers in Month N / Customers Acquired in Month 0."""
    return safe_div(active_repeat_customers, cohort_size)


def pct_change(current: float | None, previous: float | None) -> float | None:
    """Period-over-period delta as a fraction (0.12 == +12%)."""
    if current is None or previous is None or previous == 0:
        return None
    return (current - previous) / abs(previous)
