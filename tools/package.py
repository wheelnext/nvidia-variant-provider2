"""Build Python wheels and sdists with Flit from canonical JSON; never publish."""

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/dist")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="variant-provider-package-") as tmp:
        stage = Path(tmp) / "python"
        shutil.copytree(
            ROOT / "python/src/variant_provider",
            stage / "src/variant_provider",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "_data"),
        )
        for name in ("pyproject.toml", "README.md", "LICENSE"):
            shutil.copy2(ROOT / "python" / name, stage / name)
        data = stage / "src/variant_provider/_data"
        data.mkdir()
        shutil.copy2(ROOT / "json/nvidia.json", data / "nvidia.json")
        shutil.copytree(ROOT / "json/schemas", data / "schemas")
        manifest = {
            path.relative_to(data).as_posix(): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in sorted(data.rglob("*.json"))
        }
        (data / "manifest.json").write_text(
            json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
        )
        # Flit includes staged package data in both the sdist and its wheel.
        for project in (stage, ROOT / "python/compat"):
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "build",
                    "--installer=uv",
                    str(project),
                    "--outdir",
                    str(output),
                ],
                check=True,
            )
    print(output)


if __name__ == "__main__":
    main()
