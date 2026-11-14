"""Run the native ooz adapter on six upstream fixtures with Linux x86-64 g++."""
import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent))
from kolobanoveye import OOZ_PATCH, stage_ooz_sources


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dagor", type=Path, required=True)
    parser.add_argument("--ooz", type=Path, required=True)
    args = parser.parse_args()
    dagor, ooz = args.dagor.resolve(), args.ooz.resolve()
    patch = ["git", "apply", "--reverse", "--check", OOZ_PATCH]
    subprocess.run(patch, cwd=dagor, check=True)
    with tempfile.TemporaryDirectory(prefix="ke-ooz-test-") as temp:
        work = Path(temp)
        stage_ooz_sources(ooz, work)
        shutil.copyfile(TESTS / "ooz_linux_compat.h", work / "stdafx.h")
        sources = ["kraken.cpp", "lzna.cpp", "bitknit.cpp"]
        subprocess.run(["g++", "-std=c++17", "-O2", "-msse4.1", "-c", *sources], cwd=work, check=True)
        definitions = [
            "_TARGET_PC=3", "_TARGET_PC_LINUX=3", "_TARGET_SIMD_SSE=3", "DAGOR_DBGLEVEL=1",
            "_TARGET_STATIC_LIB=0", "_TARGET_64BIT=1", "__B_KERNEL_LIB",
        ]
        includes = ["engine/sharedInclude", "dagorInclude", "1stPartyLibs", "3rdPartyLibs/eastl/include"]
        subprocess.run([
            "g++", "-std=c++20", "-Wno-reorder", "-Wno-multichar",
            *[f"-D{value}" for value in definitions],
            *[f"-I{dagor / 'prog' / include}" for include in includes],
            "-include", "supp/dag_compatibility_defines_posix.h",
            dagor / "prog/engine/ioSys/oodleIoOoz.cpp", TESTS / "ooz_backend.cpp",
            *[Path(source).with_suffix(".o") for source in sources], "-o", "ooz-test",
        ], cwd=work, check=True)
        extensions = ("bitknit", "kraken", "leviathan", "lzna", "mermaid", "selkie")
        fixtures = [ooz / "testdata" / f"dickens.{ext}" for ext in extensions]
        subprocess.run([work / "ooz-test", *fixtures], cwd=work, check=True)
        digest = hashlib.sha256((work / "dickens.decoded").read_bytes()).hexdigest()
        if digest != "b24c37886142e11d0ee687db6ab06f936207aa7f2ea1fd1d9a36763c7a507e6a":
            raise ValueError(f"Unexpected decoded SHA-256 {digest}")
        print(f"OOZ_BACKEND_OK sha256={digest}")


if __name__ == "__main__":
    main()
