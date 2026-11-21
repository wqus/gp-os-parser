from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from gp_os.storage import Store


def different(record, identifier):
    copy = record.model_copy(deep=True)
    copy.listing_id = str(identifier)
    copy.url = f"https://intermark.ru/objects/arendnyy-biznes-test-{identifier}"
    copy.address = f"Москва, Тестовая улица, {identifier}"
    return copy


def test_daily_quota_is_global_atomic_and_persists_after_restart(tmp_path, record):
    stores = [Store(tmp_path), Store(tmp_path)]
    stores[0].seed({record.key: record})
    candidates = [different(record, 101), different(record, 102)]
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda pair: pair[0].add_daily(pair[1], "2026-11-21"),
                zip(stores, candidates, strict=True),
            )
        )
    assert sorted(results) == ["added", "daily_limit"]
    restarted = Store(tmp_path)
    assert len(restarted.records()) == 2
    assert restarted.add_daily(different(record, 103), "2026-11-21") == "daily_limit"
    assert restarted.add_daily(different(record, 104), "2026-11-22") == "added"


def test_identity_url_and_same_object_are_checked_before_insertion(tmp_path, record):
    store = Store(tmp_path)
    store.seed({record.key: record})
    assert store.add_daily(record, "2026-11-21") == "duplicate"
    other = different(record, 101)
    other.address = record.address
    assert store.add_daily(other, "2026-11-21") == "same_object"
    other.area_m2 = Decimal("101")
    assert store.add_daily(other, "2026-11-21") == "added"


def test_separate_processes_share_collection_lock(tmp_path):
    first, second = Store(tmp_path), Store(tmp_path)
    owner = first.acquire()
    assert owner
    assert second.acquire() is None
    first.release(owner)
    owner = second.acquire()
    assert owner
    second.release(owner)
