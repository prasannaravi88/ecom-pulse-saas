"""Canonical formula definitions from CLAUDE.md §1."""

import pytest

from app.services import finance as fin


def test_net_revenue():
    assert fin.net_revenue(10_000, 1_200, 800) == 8_000


def test_cm1_is_net_revenue_minus_cogs():
    assert fin.cm1(8_000, 2_500) == 5_500


def test_cm2_subtracts_shipping_fees_and_ads():
    assert fin.cm2(5_500, 900, 160, 2_400) == 2_040


def test_full_waterfall_chain():
    net = fin.net_revenue(gross_revenue=250_000, discounts=25_000, refunds_rto=30_000)
    assert net == 195_000
    gm = fin.cm1(net, cogs=55_000)
    assert gm == 140_000
    cm = fin.cm2(gm, shipping=18_000, payment_fees=3_900, ad_spend=65_000)
    assert cm == pytest.approx(53_100)
    assert fin.blended_mer(net, 65_000) == pytest.approx(3.0)
    assert fin.safe_div(cm, net) == pytest.approx(0.272307, rel=1e-5)


def test_negative_cm2_when_ads_exceed_margin():
    assert fin.cm2(fin.cm1(1_000, 400), 150, 20, 900) == -470


def test_blended_mer_and_true_roas():
    assert fin.blended_mer(300_000, 100_000) == pytest.approx(3.0)
    assert fin.true_roas(48_000, 30_000) == pytest.approx(1.6)


def test_true_roas_is_below_platform_roas_after_refunds():
    gross_attributed, discounts, refunds = 120_000, 12_000, 18_000
    spend, platform_claimed = 30_000, 150_000
    true = fin.true_roas(fin.net_revenue(gross_attributed, discounts, refunds), spend)
    assert true == pytest.approx(3.0)
    assert fin.platform_roas(platform_claimed, spend) == pytest.approx(5.0)
    assert true < fin.platform_roas(platform_claimed, spend)


def test_cohort_retention_rate():
    assert fin.cohort_retention_rate(87, 400) == pytest.approx(0.2175)


def test_blended_cac():
    assert fin.blended_cac(52_000, 130) == pytest.approx(400.0)


@pytest.mark.parametrize("fn", [fin.blended_mer, fin.true_roas, fin.platform_roas,
                                fin.blended_cac, fin.cohort_retention_rate, fin.safe_div])
def test_zero_denominator_returns_none_not_nan(fn):
    assert fn(100, 0) is None


def test_pct_change():
    assert fin.pct_change(120, 100) == pytest.approx(0.2)
    assert fin.pct_change(-50, -100) == pytest.approx(0.5)  # loss halved = improvement
    assert fin.pct_change(10, 0) is None
    assert fin.pct_change(None, 10) is None


def test_formulas_vectorise_over_pandas():
    import pandas as pd

    gross, disc, ret = pd.Series([100.0, 200.0]), pd.Series([10.0, 0.0]), pd.Series([0.0, 50.0])
    assert fin.net_revenue(gross, disc, ret).tolist() == [90.0, 150.0]
