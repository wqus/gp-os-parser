from datetime import UTC, datetime

import httpx
import pytest

from gp_os.client import SiteClient
from gp_os.collector import Collector
from gp_os.sources import SOURCES
from gp_os.storage import Store
from tests.conftest import listing_html


class FixtureClient:
    def __init__(self, record, *, status=200, discovery=True, changed=True):
        self.record = record
        self.status = status
        self.discovery = discovery
        self.changed = changed
        self.calls = []

    async def fetch(self, source, url, *, listing=True):
        self.calls.append((source, url, listing))
        if not listing:
            candidate = "https://intermark.ru/objects/arendnyy-biznes-test-101"
            href = candidate if self.discovery and source == "intermark" else self.record.url
            return httpx.Response(
                200, text=f'<a href="{href}">Listing</a>', request=httpx.Request("GET", url)
            )
        identifier = int(url.rsplit("-", 1)[-1])
        price = 1_300_000 if self.changed else 1_200_000
        return httpx.Response(
            self.status,
            text=listing_html(
                identifier,
                price,
                address=f"Москва, Тестовая улица, {identifier}"
                if identifier == 101
                else "Москва, Тестовая улица, 1",
            ),
            request=httpx.Request("GET", url),
        )


async def test_two_runs_update_add_one_and_do_not_duplicate(tmp_path, record):
    store = Store(tmp_path)
    store.seed({record.key: record})
    client = FixtureClient(record)
    collector = Collector(store, client)
    day = datetime(2026, 11, 21, 12, tzinfo=UTC)
    first = await collector.run(now=day)
    second = await collector.run(now=day)
    assert first["sites"]["intermark"]["changed"] == [record.key]
    assert first["sites"]["intermark"]["new"] == ["intermark:101"]
    assert sum(len(site["new"]) for site in second["sites"].values()) == 0
    assert sum(len(site["changed"]) for site in second["sites"].values()) == 0
    assert len(store.records()) == 2
    assert store.records()[record.key].price_rub == 1_300_000
    assert {source for source, _, listing in client.calls if not listing} == set(SOURCES)


async def test_site_failure_preserves_last_successful_data(tmp_path, record):
    store = Store(tmp_path)
    store.seed({record.key: record})
    client = FixtureClient(record, status=503, discovery=False)
    # Simulate the network client's real HTTP-error contract.
    original = client.fetch

    async def fail(*args, **kwargs):
        response = await original(*args, **kwargs)
        response.raise_for_status()
        return response

    client.fetch = fail
    report = await Collector(store, client).run()
    assert store.records()[record.key] == record
    assert report["sites"]["intermark"]["errors"]


async def test_first_404_is_not_removed_but_second_is(tmp_path, record):
    store = Store(tmp_path)
    store.seed({record.key: record})
    collector = Collector(store, FixtureClient(record, status=404, discovery=False))
    await collector.run()
    assert store.records()[record.key].status == "active"
    report = await collector.run()
    assert store.records()[record.key].status == "removed"
    assert report["sites"]["intermark"]["removed"] == [record.key]


async def test_410_is_confirmed_removal(tmp_path, record):
    store = Store(tmp_path)
    store.seed({record.key: record})
    report = await Collector(store, FixtureClient(record, status=410, discovery=False)).run()
    assert report["sites"]["intermark"]["removed"] == [record.key]


async def test_redirect_to_untrusted_host_is_rejected_before_request():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})

    async with SiteClient(transport=httpx.MockTransport(handler), delay=0, retries=0) as client:
        with pytest.raises(ValueError):
            await client.fetch("intermark", "https://intermark.ru/objects/test-1")
    assert len(calls) == 1


async def test_www_redirect_is_followed_without_canonicalization_loop():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if request.url.host == "s5d.ru":
            return httpx.Response(301, headers={"location": "https://www.s5d.ru/object/123"})
        return httpx.Response(
            200, text="<html>Listing</html>", headers={"content-type": "text/html"}
        )

    async with SiteClient(transport=httpx.MockTransport(handler), delay=0, retries=0) as client:
        response = await client.fetch("s5d", "https://s5d.ru/object/123")
    assert response.status_code == 200
    assert len(calls) == 2


async def test_redirect_to_another_listing_id_is_not_an_update():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            302, headers={"location": "https://intermark.ru/objects/test-2"}
        )
    )
    async with SiteClient(transport=transport, delay=0, retries=0) as client:
        with pytest.raises(ValueError, match="different listing ID"):
            await client.fetch("intermark", "https://intermark.ru/objects/test-1")
