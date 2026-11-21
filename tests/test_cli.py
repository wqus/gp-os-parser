from pathlib import Path

from gp_os import cli


def test_manual_collect_shares_server_state_and_timezone_from_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("GP_OS_DATA_DIR", str(tmp_path / "shared"))
    monkeypatch.setenv("GP_OS_SOURCES", str(tmp_path / "sources.json"))
    monkeypatch.setenv("GP_OS_TIMEZONE", "Asia/Vladivostok")
    received = {}

    async def collect(args):
        received.update(vars(args))
        return {"total": 25}

    monkeypatch.setattr(cli, "collect_once", collect)
    cli.main(["collect"])
    assert received["data_dir"] == tmp_path / "shared"
    assert received["sources"] == tmp_path / "sources.json"
    assert received["timezone"] == "Asia/Vladivostok"
    cli.main(["collect", "--data-dir", "other", "--timezone", "UTC"])
    assert received["data_dir"] == Path("other")
    assert received["timezone"] == "UTC"
