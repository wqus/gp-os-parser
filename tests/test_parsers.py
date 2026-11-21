import hashlib
from collections import Counter
from decimal import Decimal

import pytest

from gp_os.parsers import PARSERS, ParseError
from gp_os.parsers.common import amount, iso_dates, rent_values
from gp_os.sources import normalize_url
from tests.conftest import ROOT

EXPECTED = {
    "intermark:7536": ("145000000", "534", None),
    "intermark:4289": ("37900000", "102", "300000"),
    "intermark:7264": ("35000000", "90", None),
    "intermark:7267": ("57500000", "150", None),
    "intermark:7460": ("85500000", "102.8", None),
    "malina:169668": ("183600000", "477.5", "1700000"),
    "malina:190232": ("32900000", "49.5", "185000"),
    "malina:178416": ("50000000", "102.6", "300000"),
    "malina:187384": ("33000000", "65", "230000"),
    "malina:184150": ("66500000", "101.43", None),
    "invest7:868109": ("99000000", "382.1", "920000"),
    "invest7:1119719": ("46000000", "38.1", "305000"),
    "invest7:895007": ("66360000", "128.9", "400000"),
    "invest7:1115746": ("64600000", "223", "510000"),
    "invest7:1108081": ("38500000", "58.7", None),
    "s5d:162850": ("310000000", "513", "2550000"),
    "s5d:20276": ("68000000", "100.4", "380742"),
    "s5d:152526": ("40500000", "147", "320000"),
    "s5d:145572": ("51000000", "191.6", "450000"),
    "s5d:162927": ("26900000", "50.6", None),
    "nfgroup:s234197": ("250000000", "1197.60", "1830000"),
    "nfgroup:s239664": ("120000000", "46.60", "730000"),
    "nfgroup:s231528": (None, "72.80", "360000"),
    "nfgroup:s238278": ("35500000", "103.30", "280000"),
    "nfgroup:s240311": ("145000000", "542.10", None),
}


@pytest.mark.parametrize("key", EXPECTED)
def test_real_archived_listing_matches_reviewed_values(key, selection, samples):
    item = next(
        item
        for item in selection
        if f"{item['source']}:{samples[key].listing_id}" == key
        and normalize_url(item["source"], item["url"]) == samples[key].url
    )
    html = (ROOT / item["html_file"]).read_text(encoding="utf-8")
    record = PARSERS[item["source"]](item["url"], html, collected_at=samples[key].collected_at)
    expected_price, expected_area, expected_rent = EXPECTED[key]
    assert record.price_rub == (Decimal(expected_price) if expected_price else None)
    assert record.area_m2 == Decimal(expected_area)
    assert record.rent_current_rub == (Decimal(expected_rent) if expected_rent else None)
    assert record.address and record.map_url
    assert record.photos
    assert record.tenants
    assert record.html_sha256 == hashlib.sha256(html.encode("utf-8")).hexdigest()
    assert record.evidence
    assert record.status == "active"


def test_delivery_has_exactly_five_cards_per_source(samples, selection):
    assert len(samples) == len(selection) == 25
    assert Counter(record.source for record in samples.values()) == {
        "intermark": 5,
        "malina": 5,
        "invest7": 5,
        "s5d": 5,
        "nfgroup": 5,
    }
    replacement = samples["intermark:7460"]
    assert replacement.replacement_note
    assert replacement.tenants[0].status == "current"


def test_future_tenant_does_not_generate_current_income_or_payback(samples):
    for key in ["malina:184150", "invest7:1108081", "s5d:162927", "nfgroup:s240311"]:
        record = samples[key]
        assert record.rent_current_rub is None
        assert record.rent_future_rub > 0
        assert record.payback_years is None
        assert all(tenant.status == "future" for tenant in record.tenants)


def test_multi_tenant_income_is_not_arbitrarily_distributed(samples):
    tenants = samples["malina:187384"].tenants
    assert len(tenants) == 3
    assert all(tenant.rent_current_rub is None for tenant in tenants)
    assert samples["malina:187384"].rent_current_rub == Decimal("230000")
    assert len(samples["s5d:145572"].tenants) == 4
    assert len(samples["nfgroup:s231528"].tenants) == 1


def test_monthly_steps_and_turnover_formula_are_preserved(samples):
    tenant = samples["nfgroup:s240311"].tenants[0]
    assert tenant.rent_future_rub == Decimal("1400000")
    assert {step.from_month for step in tenant.rent_steps} >= {1, 7, 13, 19}
    assert tenant.turnover_formula_raw and "6%" in tenant.turnover_formula_raw
    assert all(step.amount_rub > 100 for step in tenant.rent_steps)
    assert samples["invest7:1115746"].tenants[1].rent_current_rub == Decimal("420000")


def test_rounding_and_price_conflicts_have_evidence(samples):
    assert any(item.field == "area_m2" for item in samples["malina:169668"].discrepancies)
    assert samples["malina:169668"].payback_years == Decimal("9.00")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1 250 000 руб.", "1250000"),
        ("305.000 руб.", "305000"),
        ("2,5 млн ₽", "2500000"),
        ("по запросу", None),
    ],
)
def test_russian_money_notation(raw, expected):
    assert amount(raw) == (Decimal(expected) if expected else None)


def test_partial_and_invalid_dates_are_not_invented():
    assert iso_dates("от 2024 до 2034") == []
    assert iso_dates("31.02.2026") == []
    assert iso_dates("от 04.07.2025")[0].isoformat() == "2025-07-04"


def test_new_unknown_chain_and_repeated_tenant_labels_are_not_lost():
    from gp_os.parsers.common import parse_tenants

    tenants = parse_tenants(
        'Арендатор: сетевая аптека "Неофарм". ДДА на 7 лет. '
        'Арендатор: пункт выдачи "Wildberries". ДДА на 5 лет.'
    )
    assert len(tenants) == 2
    assert "Неофарм" in tenants[0].name
    assert tenants[0].rent_current_rub is None


def test_explicit_future_lease_date_does_not_become_current_income():
    from datetime import UTC, datetime

    from tests.conftest import listing_html

    html = listing_html().replace("ДДА на 5 лет", "ДДА от 01.01.2027 на 5 лет")
    record = PARSERS["intermark"](
        "https://intermark.ru/objects/test-100",
        html,
        collected_at=datetime(2026, 11, 21, tzinfo=UTC),
    )
    assert record.tenants[0].status == "future"
    assert record.rent_current_rub is None
    assert record.rent_future_rub == Decimal("10000")
    assert record.payback_years is None


def test_turnover_percentage_is_not_read_as_rubles():
    current, _, _ = rent_values("МАП: 6% от РТО, но не менее 1 400 000 руб.")
    assert current == Decimal("1400000")


@pytest.mark.parametrize("source", PARSERS)
def test_access_challenge_is_a_parse_error(source, samples):
    record = next(record for record in samples.values() if record.source == source)
    with pytest.raises(ParseError):
        PARSERS[source](record.url, "<html><h1>Verify you are human</h1></html>")


@pytest.mark.parametrize(
    "url",
    [
        "http://intermark.ru/objects/test-1",
        "https://127.0.0.1/objects/test-1",
        "https://intermark.ru.evil.example/objects/test-1",
        "https://intermark.ru@evil.example/objects/test-1",
        "https://intermark.ru:444/objects/test-1",
    ],
)
def test_input_url_cannot_target_other_hosts(url):
    with pytest.raises(ValueError):
        normalize_url("intermark", url)


def test_tracking_and_www_do_not_create_a_new_listing():
    assert (
        normalize_url("invest7", "https://www.invest7.ru/lots/868109/?utm_source=test#photos")
        == "https://invest7.ru/lots/868109"
    )


def test_explicitly_removed_nf_listing_is_not_presented_as_active():
    html = (ROOT / "tests/fixtures/nfgroup_removed.html").read_text(encoding="utf-8")
    record = PARSERS["nfgroup"](
        "https://kf.expert/retail/knyazhnina-d-2-zhk-lefort-3-korpus-s230175", html
    )
    assert record.status == "removed"
