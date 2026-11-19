r"""Replay one locally captured Oodle block without starting DaEditor.

After a failure, the editor puts the first failed input stream at
dataset/runs/<run>/ooz-failures/failed-ooz.bin (never added to git).
Usage:
    python -m tools.ke_ooz_replay
    python -m tools.ke_ooz_replay --run dataset/runs/<run>
    python -m tools.ke_ooz_replay --unoodle D:\Projects\ke-decoders\bin\unoodle.exe
"""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CPP = ROOT / "build-logs" / "ooz-compiled" / "ke-ooz-replay.exe"
RUNS = ROOT / "dataset" / "runs"
LIMIT_PACKED = 16 * 1024 * 1024
LIMIT_UNPACKED = 256 * 1024 * 1024


def choose_run(runs: Path = RUNS) -> Path:
    candidates = [file.parent.parent for file in runs.glob("run-*/ooz-failures/failed-ooz.bin")]
    if not candidates:
        raise ValueError("Не найден сохранённый OOZ-блок. Обновите и соберите Dagor, затем повторите один запуск.")
    return max(candidates, key=lambda p: (p / "ooz-failures" / "failed-ooz.bin").stat().st_mtime_ns)


def load_capture(run: Path) -> tuple[Path, dict]:
    parent = run / "ooz-failures"
    packed_file = parent / "failed-ooz.bin"
    info_file = parent / "failed-ooz.txt"
    if not packed_file.is_file() or not info_file.is_file():
        raise ValueError(f"Нет пары failed-ooz.bin / failed-ooz.txt в {parent}")
    pairs = {}
    for line in info_file.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            pairs[key.strip()] = value.strip()
    try:
        packed, unpacked, codec = (int(pairs[x]) for x in ("packed", "unpacked", "codec"))
    except (ValueError, KeyError) as exc:
        raise ValueError(f"Неправильные метаданные OOZ: {info_file}") from exc
    if not 2 <= packed <= LIMIT_PACKED or not 1 <= unpacked <= LIMIT_UNPACKED:
        raise ValueError("OOZ size out of supported bounds")
    if packed_file.stat().st_size != packed:
        raise ValueError("Сохранённый OOZ-блок неполон или повреждён")
    with packed_file.open("rb") as stream:
        prefix = stream.read(2)
    if len(prefix) != 2 or (prefix[0] & 0x3F) != 0x0C or (prefix[1] & 0x7F) != codec:
        raise ValueError("Заголовок OOZ не соответствует метаданным")
    return packed_file, {"packed": packed, "unpacked": unpacked, "codec": codec,
                         "source": pairs.get("source", "<unknown>")}


def replay(run: Path, cpp: Path = CPP, unoodle: Path | None = None, *, keep_outputs: bool = False) -> dict:
    stream, info = load_capture(run)
    print("[KE] OOZ_INPUT", json.dumps(info, ensure_ascii=False), flush=True)
    results = {}
    outputs = []
    if not cpp.is_file():
        raise ValueError(f"Нужна сборка OOZ (отсутствует {cpp}). Выполните kolobanoveye.py build.")
    try:
        cpp_output = stream.with_name("native-decoded.raw")
        command = [str(cpp), str(stream), str(info["unpacked"])]
        if unoodle is not None or keep_outputs:
            command.append(str(cpp_output))
            outputs.append(cpp_output)
        native = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, errors="replace", timeout=90, check=False)
        print("[KE] CPP_REPLAY_EXIT", native.returncode, flush=True)
        print(native.stdout.strip(), flush=True)
        results["cpp_ok"] = native.returncode == 0
        if unoodle is not None:
            if not unoodle.is_file():
                raise ValueError(f"Rust-декодер не найден: {unoodle}")
            rust_output = stream.with_name("rust-decoded.raw")
            outputs.append(rust_output)
            rust = subprocess.run(
                [str(unoodle), str(stream), "--length", str(info["unpacked"]),
                 "--output", str(rust_output), "--verbose"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                errors="replace", timeout=90, check=False)
            print("[KE] RUST_REPLAY_EXIT", rust.returncode, flush=True)
            print(rust.stdout.strip(), flush=True)
            results["rust_ok"] = rust.returncode == 0 and rust_output.is_file() and (
                rust_output.stat().st_size == info["unpacked"])
            if results["rust_ok"] and results["cpp_ok"]:
                a = hashlib.sha256(cpp_output.read_bytes()).hexdigest()
                b = hashlib.sha256(rust_output.read_bytes()).hexdigest()
                results["sha256_equal"] = a == b
                print(f"[KE] OOZ_DECODER_OUTPUT_MATCH equal={int(a == b)}", flush=True)
            elif results["rust_ok"] and not results["cpp_ok"]:
                print("[KE] OOZ_RUST_ONLY_OK: C++ decoder incompatibility confirmed on these bytes", flush=True)
        return results
    finally:
        if not keep_outputs:
            for output in outputs:
                output.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, help="Local dataset/runs/run-... directory")
    parser.add_argument("--cpp", type=Path, default=CPP, help="C++ decoder replay executable")
    parser.add_argument("--unoodle", type=Path, help="Optional Rust oozextract unoodle.exe")
    parser.add_argument("--keep-outputs", action="store_true")
    args = parser.parse_args(argv)
    try:
        run = args.run if args.run else choose_run()
        result = replay(run.resolve(), cpp=args.cpp.resolve(),
                        unoodle=args.unoodle.resolve() if args.unoodle else None,
                        keep_outputs=args.keep_outputs)
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        parser.exit(1, f"[KE] ERROR {exc}\n")
    if result.get("sha256_equal") is False:
        parser.exit(1, "[KE] OOZ_DECODER_MISMATCH: decoded bytes differ; do not use either result as validated\n")
    if not result["cpp_ok"] and not result.get("rust_ok"):
        parser.exit(1, "[KE] OOZ_REPLAY_UNRESOLVED: neither decoder confirmed successful\n")


if __name__ == "__main__":
    main()
