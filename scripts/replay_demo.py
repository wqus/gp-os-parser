"""Offline replay: real archived HTML, one explicitly simulated price change."""

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
from bs4 import BeautifulSoup

from gp_os.client import SiteClient
from gp_os.collector import Collector, load_records
from gp_os.sources import SOURCES, normalize_url
from gp_os.storage import Store


async def demonstrate():
    records = load_records(Path("samples/gp_os.json"))
    pages = {
        record.url: Path(record.html_file).read_text(encoding="utf-8")
        for record in records.values()
    }
    target = records["intermark:4289"]
    soup = BeautifulSoup(pages[target.url], "html.parser")
    price = soup.select_one(".price-text1")
    price.clear()
    price.append("38 000 000 руб")
    pages[target.url] = str(soup)
    new_url = "https://malinaproperty.ru/property/propertiesRetail/189339"
    pages[new_url] = Path("tests/fixtures/malina_new_189339.html").read_text(encoding="utf-8")
    catalogs = {}
    for source, spec in SOURCES.items():
        links = [record.url for record in records.values() if record.source == source]
        if source == "malina":
            links.append(new_url)
        catalogs[normalize_url(source, spec.catalog, listing=False)] = "".join(
            f'<a href="{url}">Listing</a>' for url in links
        )

    def handler(request):
        url = str(request.url).replace("https://www.", "https://")
        body = pages.get(url, catalogs.get(url))
        return httpx.Response(
            200 if body is not None else 404, text=body or "", headers={"content-type": "text/html"}
        )

    with TemporaryDirectory(prefix="gp-os-replay-") as directory:
        store = Store(Path(directory))
        store.seed(records)
        async with SiteClient(delay=0, retries=0, transport=httpx.MockTransport(handler)) as client:
            collector = Collector(store, client)
            first = await collector.run()
            second = await collector.run()
        assert sum(len(site["new"]) for site in first["sites"].values()) == 1
        assert target.key in first["sites"]["intermark"]["changed"]
        assert sum(len(site["new"]) for site in second["sites"].values()) == 0
        assert sum(len(site["changed"]) for site in second["sites"].values()) == 0
        assert len(store.records()) == 26
    return {
        "mode": "controlled_offline_replay",
        "notice": (
            "Цена Intermark:4289 изменена только в памяти для демонстрации. "
            "Это не сообщение о реальном изменении цены на сайте. "
            "Исходные 25 HTML и JSON не изменяются."
        ),
        "price_before_rub": str(target.price_rub),
        "simulated_price_after_rub": "38000000",
        "first_run": first,
        "second_run": second,
    }


if __name__ == "__main__":
    result = asyncio.run(demonstrate())
    destination = Path("reports/replay.json")
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Replay verified. Report: {destination}")
