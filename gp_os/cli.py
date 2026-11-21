import argparse
import asyncio
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from gp_os.client import SiteClient
from gp_os.collector import Collector, load_records
from gp_os.parsers import PARSERS
from gp_os.sources import SOURCES, listing_id, normalize_url
from gp_os.storage import Store


async def parse_links(source: str, urls: list[str], *, html_dir: Path, offline: bool) -> dict:
    records = {}
    async with SiteClient() as client:
        for value in urls:
            url = normalize_url(source, value)
            destination = html_dir / source / f"{listing_id(source, url)}.html"
            if offline:
                html = destination.read_text(encoding="utf-8")
                collected_at = datetime.fromtimestamp(destination.stat().st_mtime, UTC)
            else:
                response = await client.fetch(source, url)
                response.raise_for_status()
                html = response.text
                collected_at = datetime.now(UTC)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(html, encoding="utf-8")
            record = PARSERS[source](url, html, collected_at=collected_at)
            record.html_file = destination.as_posix()
            records[record.key] = record.model_dump(mode="json")
    return records


def parse_main(source: str, argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=f"Parse {SOURCES[source].name} listing URLs")
    parser.add_argument("urls", nargs="*")
    parser.add_argument("--input", type=Path, help="UTF-8 JSON array of URLs")
    parser.add_argument("--output", type=Path, default=Path(f"{source}.json"))
    parser.add_argument("--html-dir", type=Path, default=Path("samples/html"))
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args(argv)
    urls = args.urls + (json.loads(args.input.read_text(encoding="utf-8")) if args.input else [])
    if not urls or not all(isinstance(url, str) for url in urls):
        parser.error("Provide listing URLs or --input with a JSON array")
    records = asyncio.run(parse_links(source, urls, html_dir=args.html_dir, offline=args.offline))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved {len(records)} records to {args.output}")


async def collect_once(args) -> dict:
    store = Store(args.data_dir)
    if args.seed.exists():
        store.seed(load_records(args.seed))
    config = json.loads(args.sources.read_text(encoding="utf-8")) if args.sources.exists() else {}
    async with SiteClient() as client:
        return await Collector(store, client, timezone=args.timezone, config=config).run()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="GP OS listing collector")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8001)
    collect = commands.add_parser("collect")
    collect.add_argument("--data-dir", type=Path, default=Path(os.getenv("GP_OS_DATA_DIR", "data")))
    collect.add_argument("--seed", type=Path, default=Path("samples/gp_os.json"))
    collect.add_argument(
        "--sources", type=Path, default=Path(os.getenv("GP_OS_SOURCES", "sources.json"))
    )
    collect.add_argument("--timezone", default=os.getenv("GP_OS_TIMEZONE", "Europe/Moscow"))
    collect.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.command == "serve":
        import uvicorn

        uvicorn.run("gp_os.web:create_app", factory=True, host=args.host, port=args.port)
    else:
        report = asyncio.run(collect_once(args))
        payload = json.dumps(report, ensure_ascii=False, indent=2)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(payload, encoding="utf-8")
            print(f"Completed: {report['total']} records. Report: {args.report}")
        else:
            print(payload)
