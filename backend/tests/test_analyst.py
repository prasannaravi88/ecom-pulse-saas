import pytest

from app.services.analyst import INTENTS, format_money, is_write_attempt, route


@pytest.mark.parametrize("q,expected", [
    ("Which SKUs are bleeding money?", "margin_leaks"),
    ("did diwali cohorts recover their CAC", "festive_payback"),
    ("facebook vs google roas", "channel_roas"),
    ("why did CM2 drop vs last period", "cm2_bridge"),
    ("what is cash on delivery RTO costing", "cod_rto"),
    ("where should I scale budget", "hero_skus"),
])
def test_route(q, expected):
    assert route(q).id == expected


def test_every_suggested_prompt_routes_to_itself():
    for intent in INTENTS:
        assert route(intent.question).id == intent.id


def test_route_unknown():
    assert route("what's the weather in Mumbai") is None


@pytest.mark.parametrize("q", [
    "delete all orders", "DROP TABLE orders", "update the price of SKU X to 10",
    "select * from orders", "show margins; drop table x", "set cogs to 0 for every sku",
])
def test_write_attempts_blocked(q):
    assert is_write_attempt(q)


@pytest.mark.parametrize("q", [
    "What changed in contribution margin?", "Which SKUs are leaking margin?",
    "How did spend change vs last period?", "give me an update on cohorts",
])
def test_read_questions_allowed(q):
    assert not is_write_attempt(q)


def test_format_money():
    assert format_money(12_345_678, "INR") == "₹1.23 Cr"
    assert format_money(-546_000, "INR") == "−₹5.46 L"
    assert format_money(473.2, "INR") == "₹473"
    assert format_money(2_300_000, "USD") == "$2.30M"
    assert format_money(23_462, "USD") == "$23.5K"
    assert format_money(19.06, "USD") == "$19.06"
