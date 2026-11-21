import json
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from gp_os.models import Listing


def object_fingerprint(record: Listing) -> str | None:
    if not record.address or not record.area_m2 or not record.tenants:
        return None
    address = record.address.casefold().replace("ё", "е")
    address = re.sub(r"\b(?:город|г|улица|ул|дом|д|корпус|корп|строение|стр)\b[.]?", "", address)
    address = re.sub(r"\W", "", address)
    names = sorted(
        re.sub(r"\W", "", (tenant.name or "").casefold().replace("ё", "е"))
        for tenant in record.tenants
    )
    if not any(names):
        return None
    return f"{address}|{record.area_m2.normalize()}|{','.join(names)}"


class Store:
    def __init__(self, directory: Path):
        self.directory = directory.resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.database = self.directory / "state.sqlite3"
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS listings (
                    key TEXT PRIMARY KEY, url TEXT NOT NULL UNIQUE,
                    body TEXT NOT NULL, missing_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS additions (
                    day TEXT PRIMARY KEY, listing_key TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS mutex (
                    id INTEGER PRIMARY KEY CHECK(id=1), owner TEXT NOT NULL, expires REAL NOT NULL
                );
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def records(self) -> dict[str, Listing]:
        with self.connect() as db:
            return {
                row["key"]: Listing.model_validate_json(row["body"])
                for row in db.execute("SELECT key,body FROM listings ORDER BY key")
            }

    def seed(self, records: dict[str, Listing]) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT COUNT(*) FROM listings").fetchone()[0]:
                return
            for key, record in records.items():
                if key != record.key:
                    raise ValueError("Record key does not match source and listing_id")
                db.execute(
                    "INSERT INTO listings(key,url,body) VALUES (?,?,?)",
                    (key, record.url, record.model_dump_json()),
                )
        self.export()

    def save(self, record: Listing) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE listings SET url=?,body=?,missing_count=0 WHERE key=?",
                (record.url, record.model_dump_json(), record.key),
            )

    def add_daily(self, record: Listing, day: str) -> str:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT key,url,body FROM listings").fetchall()
            fingerprint = object_fingerprint(record)
            for row in rows:
                other = Listing.model_validate_json(row["body"])
                if row["key"] == record.key or row["url"] == record.url:
                    return "duplicate"
                if fingerprint and fingerprint == object_fingerprint(other):
                    return "same_object"
            if db.execute("SELECT 1 FROM additions WHERE day=?", (day,)).fetchone():
                return "daily_limit"
            db.execute("INSERT INTO additions(day,listing_key) VALUES (?,?)", (day, record.key))
            db.execute(
                "INSERT INTO listings(key,url,body) VALUES (?,?,?)",
                (record.key, record.url, record.model_dump_json()),
            )
            return "added"

    def quota_used(self, day: str) -> bool:
        with self.connect() as db:
            return db.execute("SELECT 1 FROM additions WHERE day=?", (day,)).fetchone() is not None

    def missing(self, key: str) -> int:
        with self.connect() as db:
            db.execute("UPDATE listings SET missing_count=missing_count+1 WHERE key=?", (key,))
            return db.execute("SELECT missing_count FROM listings WHERE key=?", (key,)).fetchone()[
                0
            ]

    def acquire(self) -> str | None:
        owner = uuid.uuid4().hex
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM mutex WHERE expires < ?", (time.time(),))
            if db.execute("SELECT 1 FROM mutex").fetchone():
                return None
            db.execute("INSERT INTO mutex VALUES (1,?,?)", (owner, time.time() + 900))
        return owner

    def renew(self, owner: str) -> None:
        with self.connect() as db:
            changed = db.execute(
                "UPDATE mutex SET expires=? WHERE owner=?", (time.time() + 900, owner)
            ).rowcount
            if changed != 1:
                raise RuntimeError("Collection lock was lost")

    def release(self, owner: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM mutex WHERE owner=?", (owner,))

    def save_run(self, report: dict) -> None:
        with self.connect() as db:
            cursor = db.execute(
                "INSERT INTO runs(body) VALUES (?)", (json.dumps(report, ensure_ascii=False),)
            )
            report["id"] = cursor.lastrowid
            db.execute(
                "UPDATE runs SET body=? WHERE id=?",
                (json.dumps(report, ensure_ascii=False), report["id"]),
            )

    def runs(self, limit: int = 10) -> list[dict]:
        with self.connect() as db:
            return [
                json.loads(row[0])
                for row in db.execute("SELECT body FROM runs ORDER BY id DESC LIMIT ?", (limit,))
            ]

    def is_running(self) -> bool:
        with self.connect() as db:
            return (
                db.execute("SELECT 1 FROM mutex WHERE expires >= ?", (time.time(),)).fetchone()
                is not None
            )

    def export(self) -> Path:
        content = {key: value.model_dump(mode="json") for key, value in self.records().items()}
        destination = self.directory / "gp_os.json"
        temporary = self.directory / f".export-{uuid.uuid4().hex}.tmp"
        temporary.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(destination)
        return destination
