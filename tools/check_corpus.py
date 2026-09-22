"""Compare case discovery and every CLI replay result across implementations."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def run(command):
    return json.loads(subprocess.check_output(list(map(str, command)), text=True))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rust-bin", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.rust_bin is None:
        subprocess.run(
            [
                "cargo",
                "build",
                "--locked",
                "--manifest-path",
                str(root / "rust/Cargo.toml"),
            ],
            check=True,
        )
        suffix = ".exe" if os.name == "nt" else ""
        args.rust_bin = root / "rust/target/debug" / f"variant-provider-rs{suffix}"
    cases = root / "json/tests/cases"
    py = [sys.executable, "-m", "variant_provider.cli"]
    rust = [args.rust_bin.resolve()]
    a = run(py + ["inventory", "--cases", cases])
    b = run(rust + ["inventory", "--cases", cases])
    assert a == b, (a, b)
    for name in a["cases"]:
        case = cases / name
        expected = {
            "results": [s["expected"] for s in json.loads(case.read_text())["steps"]]
        }
        p = run(py + ["replay", "--case", case])
        r = run(rust + ["replay", "--case", case])
        assert p == r == expected, name
    print(f"Both runners discovered and passed the same {len(a['cases'])} cases")


if __name__ == "__main__":
    main()
