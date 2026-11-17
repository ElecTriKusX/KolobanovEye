"""Discover installed War Thunder ground maps and manage a local allowlist.

Discovery is a cheap file/header inventory, NOT proof that Dagor can render a map.
The active frame renderer still uses Normandy until the next implementation gate.
"""
import argparse
import json
import os
import re
from pathlib import Path

WHITELIST_FILE = Path(__file__).resolve().with_name("map-whitelist.local.json")
MAP_ID = re.compile(r"levels/avg_[A-Za-z0-9_.-]+\.bin\Z", re.IGNORECASE)
DBLD_MAGIC = b"DBLD3x64"


def scan_maps(game):
    """Return alphabetically sorted avg_*.bin files directly under <game>/levels."""
    levels = Path(game) / "levels"
    if not levels.is_dir():
        raise ValueError(f"Папка карт не найдена: {levels}")
    result = []
    for entry in levels.iterdir():
        if entry.is_symlink() or not entry.is_file():
            continue
        name = entry.name
        if not name.casefold().startswith("avg_") or not name.casefold().endswith(".bin"):
            continue
        try:
            size = entry.stat().st_size
            with entry.open("rb") as file:
                signature = file.read(8)
            status = "header_ok" if signature == DBLD_MAGIC else "invalid_header"
        except OSError:
            size, status = None, "read_error"
        result.append({
            "id": f"levels/{name}",
            "name": entry.stem,
            "size_bytes": size,
            "status": status,
        })
    return sorted(result, key=lambda item: (item["id"].casefold(), item["id"]))


def _checked_ids(ids):
    if not isinstance(ids, list):
        raise ValueError("Белый список карт должен быть массивом")
    unique = set()
    result = []
    for map_id in ids:
        if not isinstance(map_id, str) or MAP_ID.fullmatch(map_id) is None:
            raise ValueError(f"Некорректный ID карты: {map_id!r}")
        if map_id.casefold() in unique:
            raise ValueError(f"Повтор карты в белом списке: {map_id}")
        unique.add(map_id.casefold())
        result.append(map_id)
    return result


def load_whitelist(path=WHITELIST_FILE):
    """Return list of map IDs, or None if no local selection has been saved."""
    path = Path(path)
    if not path.exists():
        return None
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Невозможно прочитать белый список {path}: {exc}") from exc
    if not isinstance(obj, dict) or obj.get("version") != 1:
        raise ValueError("Неизвестный формат белого списка карт (требуется version: 1)")
    return _checked_ids(obj.get("maps"))


def save_whitelist(ids, path=WHITELIST_FILE):
    """Atomically save selection without touching any War Thunder files."""
    path = Path(path)
    checked = _checked_ids(ids)
    payload = json.dumps({"version": 1, "maps": checked}, indent=2, ensure_ascii=False) + "\n"
    temp = path.with_name(path.name + ".tmp")
    try:
        temp.write_text(payload, encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def initial_whitelist(discovered):
    """On first use, preselect Normandy when available, otherwise first valid avg map."""
    valid = [entry["id"] for entry in discovered if entry["status"] == "header_ok"]
    if not valid:
        return []
    return [next((name for name in valid if name.casefold() == "levels/avg_normandy.bin"), valid[0])]


def available_whitelist(discovered, selected):
    """Keep discovery order and only allow inventory entries with a supported header."""
    allowed = {name.casefold() for name in _checked_ids(selected)}
    return [entry["id"] for entry in discovered if entry["status"] == "header_ok" and entry["id"].casefold() in allowed]


def plan_equal_allocation(total, map_ids):
    """Deterministically distribute images: (7, 3 maps) -> [3, 2, 2]."""
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise ValueError("Количество изображений должно быть неотрицательным целым")
    ids = _checked_ids(map_ids)
    if not ids and total:
        raise ValueError("Для генерации нужно выбрать хотя бы одну карту")
    if not ids:
        return []
    base, remainder = divmod(total, len(ids))
    return [(map_id, base + (i < remainder)) for i, map_id in enumerate(ids)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game", type=Path, default=Path(r"D:\Games\WarThunder"))
    parser.add_argument("--show-whitelist", action="store_true")
    args = parser.parse_args()
    try:
        maps = scan_maps(args.game)
        selected = load_whitelist()
        if selected is None:
            selected = initial_whitelist(maps)
        print(f"[KE] avg maps found: {len(maps)}")
        for item in maps:
            flag = "*" if item["id"] in selected else " "
            print(f"[{flag}] {item['id']} ({item['status']})")
        if args.show_whitelist:
            print("[KE] Available whitelist:", available_whitelist(maps, selected))
    except ValueError as exc:
        parser.exit(1, f"[KE] ERROR {exc}\n")


if __name__ == "__main__":
    main()
