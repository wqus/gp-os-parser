from datetime import UTC, datetime

import httpx

from gp_os.web import create_app, next_run_at


async def test_dashboard_download_and_busy_lock(tmp_path):
    app = create_app(data_dir=tmp_path, schedule_enabled=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            assert (await client.get("/")).status_code == 200
            status = (await client.get("/api/status")).json()
            assert status["total"] == 25
            assert len(status["sources"]) == 5
            download = await client.get("/download")
            assert len(download.json()) == 25
            assert "attachment" in download.headers["content-disposition"]
            owner = app.state.store.acquire()
            try:
                assert (await client.post("/api/run")).status_code == 409
            finally:
                app.state.store.release(owner)


def test_daily_schedule_uses_configured_timezone_and_next_day():
    before = datetime(2026, 11, 21, 23, 59, tzinfo=UTC)
    assert next_run_at(before, "03:00", "Europe/Moscow") == datetime(2026, 11, 22, 0, tzinfo=UTC)
    at = datetime(2026, 11, 22, 0, tzinfo=UTC)
    assert next_run_at(at, "03:00", "Europe/Moscow") == datetime(2026, 11, 23, 0, tzinfo=UTC)
