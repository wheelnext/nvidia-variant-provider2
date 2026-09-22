"""Explicitly regenerate NVIDIA expectations from the unchanged oracle only.

This tool does not run during ordinary tests. Review resulting expectation diffs.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", type=Path, default=ROOT / "reference-design")
    args = parser.parse_args()
    revision = subprocess.check_output(
        ["git", "-C", str(args.reference), "rev-parse", "HEAD"], text=True
    ).strip()
    expected = "ffea5d3bf372d7a3b4504b4ee295282417af767c"
    if revision != expected:
        raise SystemExit(f"Wrong reference revision: {revision}")
    for path in sorted((ROOT / "json/tests/cases").glob("*.json")):
        case = json.loads(path.read_text())
        if case["definition"] != "nvidia.json":
            continue
        output = subprocess.check_output(
            [
                sys.executable,
                str(
                    ROOT / "python/src/variant_provider/validation/reference_worker.py"
                ),
                "--reference",
                str(args.reference),
                "--bindings",
                str(ROOT / "json/tests/reference-bindings.json"),
                "--request",
                str(path),
            ],
            text=True,
        )
        result = json.loads(output)
        for step, value in zip(case["steps"], result["results"], strict=True):
            step["expected"] = value
        case["oracle"] = {
            "revision": revision,
            "packaging_revision": "3b77a26f5a27473ad3b08194d773f325d018a2d0",
            "source": "unmodified reference, replayed NVML boundary",
        }
        path.write_text(json.dumps(case, indent=2) + "\n")
        print(path.name)


if __name__ == "__main__":
    main()
