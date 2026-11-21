import json
from pathlib import Path

import pytest

from gp_os.collector import load_records
from gp_os.parsers.intermark import parse

ROOT = Path(__file__).resolve().parents[1]


def listing_html(identifier=100, price=1_200_000, *, address="Москва, Тестовая улица, 1"):
    return f"""<!doctype html><html><title>Тестовый объект</title><body>
    <div class="price-text1">{price} руб</div>
    <div class="object-detail"><h1>Арендный бизнес, 100 кв.м, {address}</h1>
    <div class="with-styled-list">Тестовая карточка {identifier} для автоматической проверки.
    Арендатор: Тестовый магазин. МАП: 10 000 руб. ДДА на 5 лет.</div></div>
    </body></html>"""


@pytest.fixture
def selection():
    return json.loads((ROOT / "samples/selection.json").read_text(encoding="utf-8"))


@pytest.fixture
def samples():
    return load_records(ROOT / "samples/gp_os.json")


@pytest.fixture
def record():
    return parse("https://intermark.ru/objects/arendnyy-biznes-test-100", listing_html())
