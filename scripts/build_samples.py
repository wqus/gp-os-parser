import json
from datetime import UTC, datetime
from pathlib import Path

from gp_os.parsers import PARSERS


def main():
    directory = Path("samples")
    selection = json.loads((directory / "selection.json").read_text(encoding="utf-8"))
    records = {}
    for item in selection:
        path = Path(item["html_file"])
        captured = datetime.fromisoformat(item["collected_at"]).astimezone(UTC)
        record = PARSERS[item["source"]](
            item["url"], path.read_text(encoding="utf-8"), collected_at=captured
        )
        record.requested_category = item["category"]
        record.replacement_note = item.get("replacement_note")
        record.html_file = path.as_posix()
        if record.key in records:
            raise ValueError(f"Duplicate key: {record.key}")
        records[record.key] = record.model_dump(mode="json")
    if len(records) != 25:
        raise ValueError("The delivery must contain exactly 25 sample cards")
    (directory / "gp_os.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Built {len(records)} records from archived HTML")


if __name__ == "__main__":
    main()
