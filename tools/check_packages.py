"""Verify artifacts in temporary directories, without editable source imports."""

import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "artifacts/dist"


def run(command, **kw):
    return subprocess.check_output(list(map(str, command)), text=True, **kw)


def main():
    engine = next(DIST.glob("variant_provider-*.whl"))
    compat = next(DIST.glob("nvidia_variant_provider-*.whl"))
    sdist = next(DIST.glob("variant_provider-*.tar.gz"))
    expected = hashlib.sha256((ROOT / "json/nvidia.json").read_bytes()).hexdigest()
    with zipfile.ZipFile(engine) as archive:
        names = archive.namelist()
        assert not any(
            ".local" in n or "development-trust" in n or "/tests/" in n for n in names
        )
        data = archive.read("variant_provider/_data/nvidia.json")
        assert hashlib.sha256(data).hexdigest() == expected
    with tempfile.TemporaryDirectory(prefix="variant-installed-") as tmp:
        tmp = Path(tmp)
        subprocess.run(
            ["uv", "venv", str(tmp / "venv"), "--python", sys.executable], check=True
        )
        python = (
            tmp / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        )
        subprocess.run(
            ["uv", "pip", "install", "--python", str(python), str(engine), str(compat)],
            check=True,
        )
        meta = json.loads(
            run([python, "-m", "variant_provider.cli", "validate"], cwd=tmp)
        )
        assert meta["definition_sha256"] == expected
        probe = "from nvidia_variant_provider.plugin import NvidiaVariantPlugin as P; assert len(P.generate_all_umd_values()) == 110; assert len(P.generate_all_sm_values()) == 160"
        run([python, "-c", probe], cwd=tmp)
        # Rebuild the source distribution after extraction away from json/.
        source = tmp / "source"
        source.mkdir()
        with tarfile.open(sdist) as archive:
            archive.extractall(source, filter="data")
        project = next(source.iterdir())
        subprocess.run(
            [
                sys.executable,
                "-m",
                "build",
                "--installer=uv",
                "--wheel",
                str(project),
                "--outdir",
                str(tmp / "rebuilt"),
            ],
            check=True,
            cwd=tmp,
        )
        rebuilt = next((tmp / "rebuilt").glob("*.whl"))
        with zipfile.ZipFile(rebuilt) as archive:
            assert (
                hashlib.sha256(
                    archive.read("variant_provider/_data/nvidia.json")
                ).hexdigest()
                == expected
            )
    print("Python wheels and isolated sdist rebuild verified")


if __name__ == "__main__":
    main()
