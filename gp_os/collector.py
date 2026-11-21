import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup

from gp_os.models import Listing
from gp_os.parsers import PARSERS, ParseError
from gp_os.sources import SOURCES, listing_id, normalize_url
from gp_os.storage import Store


class AlreadyRunning(RuntimeError):
    pass


def meaningful(record: Listing) -> dict:
    return record.model_dump(
        exclude={"collected_at", "last_successful_check_at", "html_file", "html_sha256"}
    )


class Collector:
    def __init__(
        self,
        store: Store,
        client,
        *,
        timezone: str = "Europe/Moscow",
        max_candidates: int = 8,
        config: dict | None = None,
    ):
        self.store = store
        self.client = client
        self.timezone = ZoneInfo(timezone)
        self.max_candidates = max_candidates
        self.config = config or {}

    def archive(self, record: Listing, html: str) -> None:
        relative = Path(
            "html", record.source, f"{record.listing_id}-{record.html_sha256[:16]}.html"
        )
        target = self.store.directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_text(html, encoding="utf-8")
        record.html_file = relative.as_posix()

    async def run(self, *, now: datetime | None = None) -> dict:
        started = now or datetime.now(UTC)
        owner = self.store.acquire()
        if owner is None:
            raise AlreadyRunning("A collection is already in progress")
        day = started.astimezone(self.timezone).date().isoformat()
        report = {
            "started_at": started.isoformat(),
            "finished_at": None,
            "day": day,
            "sites": {
                name: {
                    "name": spec.name,
                    "new": [],
                    "changed": [],
                    "removed": [],
                    "checked": 0,
                    "unchanged": 0,
                    "duplicates": [],
                    "errors": [],
                }
                for name, spec in SOURCES.items()
            },
        }
        try:
            existing = self.store.records()
            for key, previous in existing.items():
                self.store.renew(owner)
                stats = report["sites"][previous.source]
                try:
                    response = await self.client.fetch(previous.source, previous.url)
                    if response.status_code in (404, 410):
                        count = self.store.missing(key)
                        if response.status_code == 410 or count >= 2:
                            if previous.status != "removed":
                                previous.status = "removed"
                                previous.last_successful_check_at = datetime.now(UTC)
                                previous.warnings.append(
                                    f"Снятие подтверждено: HTTP {response.status_code}; "
                                    f"проверок отсутствия: {count}."
                                )
                                self.store.save(previous)
                                stats["removed"].append(key)
                        else:
                            stats["errors"].append(
                                {
                                    "url": previous.url,
                                    "message": (
                                        "Первый HTTP 404; снятие пока не подтверждено, "
                                        "данные сохранены."
                                    ),
                                }
                            )
                        stats["checked"] += 1
                        continue
                    soup = BeautifulSoup(response.text, "html.parser")
                    removed = soup.find(
                        string=lambda s: (
                            s
                            and s.strip()
                            in (
                                "Объект снят с публикации",
                                "Объект продан",
                                "Объявление снято с публикации",
                            )
                        )
                    )
                    if removed:
                        if previous.status != "removed":
                            previous.status = "removed"
                            previous.last_successful_check_at = datetime.now(UTC)
                            self.store.save(previous)
                            stats["removed"].append(key)
                        stats["checked"] += 1
                        continue
                    current = PARSERS[previous.source](previous.url, response.text)
                    if current.key != key:
                        raise ParseError("The response identifies a different listing")
                    current.collected_at = previous.collected_at
                    current.requested_category = previous.requested_category
                    current.replacement_note = previous.replacement_note
                    self.archive(current, response.text)
                    if meaningful(previous) != meaningful(current):
                        stats["changed"].append(key)
                    else:
                        stats["unchanged"] += 1
                    stats["checked"] += 1
                    self.store.save(current)
                except (httpx.HTTPError, ValueError, KeyError) as error:
                    stats["errors"].append(
                        {"url": previous.url, "message": f"{type(error).__name__}: {error}"}
                    )

            for source, spec in SOURCES.items():
                self.store.renew(owner)
                stats = report["sites"][source]
                catalogs = self.config.get(source, {}).get("catalog_urls", [spec.catalog])
                urls = []
                for catalog in catalogs:
                    try:
                        response = await self.client.fetch(source, catalog, listing=False)
                        response.raise_for_status()
                        soup = BeautifulSoup(response.text, "html.parser")
                        found = []
                        for anchor in soup.select("a[href]"):
                            try:
                                found.append(
                                    normalize_url(
                                        source, urljoin(str(response.url), anchor["href"])
                                    )
                                )
                            except ValueError:
                                continue
                        if not found:
                            raise ParseError(
                                "No listing links found in catalog; possible layout change"
                            )
                        urls.extend(found)
                    except (httpx.HTTPError, ValueError, KeyError) as error:
                        stats["errors"].append(
                            {"url": catalog, "message": f"{type(error).__name__}: {error}"}
                        )
                known = self.store.records()
                candidates = [
                    url
                    for url in dict.fromkeys(urls)
                    if f"{source}:{listing_id(source, url)}" not in known
                ]
                stats["discovered"] = len(candidates)
                if self.store.quota_used(day):
                    continue
                for url in candidates[: self.max_candidates]:
                    try:
                        response = await self.client.fetch(source, url)
                        if response.status_code in (404, 410):
                            continue
                        record = PARSERS[source](url, response.text)
                        if not record.tenants or record.status != "active":
                            continue
                        self.archive(record, response.text)
                        result = self.store.add_daily(record, day)
                        if result == "added":
                            stats["new"].append(record.key)
                            break
                        if result in ("duplicate", "same_object"):
                            stats["duplicates"].append({"url": url, "reason": result})
                    except (httpx.HTTPError, ValueError, KeyError) as error:
                        stats["errors"].append(
                            {"url": url, "message": f"{type(error).__name__}: {error}"}
                        )
            report["finished_at"] = datetime.now(UTC).isoformat()
            report["total"] = len(self.store.records())
            self.store.export()
            self.store.save_run(report)
            return report
        finally:
            self.store.release(owner)


def load_records(path: Path) -> dict[str, Listing]:
    return {
        key: Listing.model_validate(value)
        for key, value in json.loads(path.read_text(encoding="utf-8")).items()
    }
