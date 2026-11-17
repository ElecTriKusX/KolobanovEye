"""Freeze the previously compiled OOZ decoder sources inside this checkout.

Default source: build-logs/ooz-local from the successful OOZ library build.
The pinned files are verified using vendor/ooz.lock.json; no network used.
Upstream OOZ has no clear redistribution license: vendor/ooz is gitignored.
"""
import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
LOCK = PROJECT / "vendor" / "ooz.lock.json"
VENDOR = PROJECT / "vendor" / "ooz"
DEFAULT_SOURCE = PROJECT / "build-logs" / "ooz-local"


def required_hashes():
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    return lock["files"]


def verify_files(root):
    """Fail on any missing or modified source, without trusting external versions."""
    root = Path(root)
    expected = required_hashes()
    for name, digest in expected.items():
        file = root / name
        if not file.is_file() or file.is_symlink():
            raise ValueError(f"OOZ source missing or symlink: {file}")
        actual = hashlib.sha256(file.read_bytes()).hexdigest()
        if actual != digest:
            raise ValueError(f"OOZ SHA256 mismatch: {file.name}; expected {digest}, got {actual}")
    return expected


def verify_vendor():
    return verify_files(VENDOR)


def install(source=DEFAULT_SOURCE):
    """Copy a checked snapshot to vendor/ooz, never silently overwrite another version."""
    source = Path(source).resolve()
    expected = verify_files(source)
    if VENDOR.exists():
        verify_vendor()
        print(f"[KE] OOZ_VENDOR_OK already pinned at {VENDOR}")
        return VENDOR
    VENDOR.parent.mkdir(parents=True, exist_ok=True)
    temp = VENDOR.with_name("ooz.pending." + str(os.getpid()))
    if temp.exists():
        raise ValueError(f"OOZ staging already exists: {temp}")
    temp.mkdir()
    try:
        for name in expected:
            shutil.copyfile(source / name, temp / name)
        verify_files(temp)
        temp.rename(VENDOR)
    finally:
        if temp.exists():
            shutil.rmtree(temp)
    print(f"[KE] OOZ_VENDOR_OK pinned={len(expected)} source_files path={VENDOR}")
    return VENDOR


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-dir", type=Path, default=DEFAULT_SOURCE,
                        help="Directory containing pre-staged decode-only OOZ source files")
    args = parser.parse_args()
    try:
        install(args.from_dir)
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"[KE] ERROR {exc}\n")


if __name__ == "__main__":
    main()
