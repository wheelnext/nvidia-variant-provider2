"""Run on Linux and Windows NVIDIA hosts; a no-GPU run never certifies parity."""

from __future__ import annotations

import argparse
import datetime
import json
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

from variant_provider.definition import bundled_bytes, strict_json

REFERENCE = "ffea5d3bf372d7a3b4504b4ee295282417af767c"
PACKAGING = "3b77a26f5a27473ad3b08194d773f325d018a2d0"
OVERRIDES = (
    "NV_VARIANT_PROVIDER_FORCE_CUDA_DRIVER_VERSION",
    "NV_VARIANT_PROVIDER_FORCE_SM_ARCH",
)


def difference(a, b, path=""):
    """Ordered, type-aware structural comparison; no sorting of provider lists."""
    if type(a) is not type(b):
        return [{"path": path, "reference": a, "actual": b}]
    if isinstance(a, dict):
        result = []
        for key in sorted(a.keys() | b.keys()):
            child = f"{path}/{key}"
            if key not in a or key not in b:
                result.append(
                    {
                        "path": child,
                        "reference": a.get(key),
                        "actual": b.get(key),
                        "missing": "reference" if key not in a else "actual",
                    }
                )
            else:
                result.extend(difference(a[key], b[key], child))
        return result
    if isinstance(a, list):
        result = []
        if len(a) != len(b):
            result.append(
                {"path": path + "/length", "reference": len(a), "actual": len(b)}
            )
        for i, (x, y) in enumerate(zip(a, b)):
            result.extend(difference(x, y, f"{path}/{i}"))
        return result
    return [] if a == b else [{"path": path, "reference": a, "actual": b}]


def command(args, env=None):
    process = subprocess.run(
        list(map(str, args)),
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
        check=False,
    )
    if process.returncode:
        raise RuntimeError(f"Command failed: {args}\n{process.stderr}")
    return json.loads(process.stdout)


def revision(directory):
    return subprocess.check_output(
        ["git", "-C", str(directory), "rev-parse", "HEAD"], text=True
    ).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, default=Path("reference-design"))
    parser.add_argument(
        "--bindings", type=Path, default=Path("json/tests/reference-bindings.json")
    )
    parser.add_argument("--rust-bin", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--allow-no-hardware",
        action="store_true",
        help="Smoke test only; never emits a passing hardware-validation status",
    )
    args = parser.parse_args()
    reference = args.reference.resolve()
    rust = args.rust_bin.resolve()
    if revision(reference) != REFERENCE:
        parser.error("Reference revision does not match the frozen oracle")
    if revision(reference / "nvidia_variant_provider/vendor/packaging") != PACKAGING:
        parser.error("Initialize the pinned packaging submodule")
    for directory in (
        reference,
        reference / "nvidia_variant_provider/vendor/packaging",
    ):
        dirty = subprocess.check_output(
            [
                "git",
                "-C",
                str(directory),
                "status",
                "--porcelain",
                "--untracked-files=no",
            ],
            text=True,
        )
        if dirty:
            parser.error(f"Oracle has tracked modifications: {directory}")
    env = os.environ.copy()
    for key in OVERRIDES:
        env.pop(key, None)
    python = [sys.executable, "-m", "variant_provider.cli"]
    py_meta = command(python + ["validate"], env)
    rs_meta = command([rust, "validate"], env)
    mismatches = difference(py_meta, rs_meta, "/definition_metadata")
    definition = strict_json(bundled_bytes("nvidia.json"))
    methods = list(definition["exports"])
    scenarios = [(f"individual/{m}", [m], {}) for m in methods]
    scenarios += [
        ("sequence", methods + list(reversed(methods)), {}),
        ("overrides", methods + methods, dict(zip(OVERRIDES, ["12.7", "8.9"]))),
        ("range_bypass", methods, dict(zip(OVERRIDES, ["16.25", "13.5"]))),
        ("invalid_override", methods, dict(zip(OVERRIDES, ["invalid", "bad"]))),
    ]
    worker = Path(__file__).with_name("reference_worker.py")
    report = {
        "report_version": 1,
        "recorded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "host": {
            "system": platform.system(),
            "machine": platform.machine(),
            "release": platform.release(),
            "python": sys.version,
        },
        "reference_revision": REFERENCE,
        "packaging_revision": PACKAGING,
        "definition": py_meta,
        "scenarios": [],
    }
    genuine_traces = []
    with tempfile.TemporaryDirectory(prefix="variant-comparison-") as directory:
        request_path = Path(directory) / "request.json"
        for label, sequence, overrides in scenarios:
            request = {
                "environment": overrides,
                "steps": [{"method": m} for m in sequence],
            }
            request_path.write_text(json.dumps(request), encoding="utf-8")
            this_env = {**env, **overrides}
            baseline = command(
                [
                    sys.executable,
                    worker,
                    "--reference",
                    reference,
                    "--bindings",
                    args.bindings.resolve(),
                    "--request",
                    request_path,
                    "--live",
                ],
                this_env,
            )
            py = command(python + ["invoke", "--method", ",".join(sequence)], this_env)
            rs = command([rust, "invoke", "--method", ",".join(sequence)], this_env)
            for backend, actual in [("python", py), ("rust", rs)]:
                mismatches.extend(
                    {"scenario": label, "backend": backend, **d}
                    for d in difference(
                        baseline["results"], actual["results"], "/results"
                    )
                )
            mismatches.extend(
                {"scenario": label, **d}
                for d in difference(
                    baseline["metadata"],
                    {
                        k: py_meta["provider"][k]
                        for k in ("namespace", "is_build_plugin")
                    },
                    "/provider",
                )
            )
            report["scenarios"].append(
                {
                    "name": label,
                    "methods": sequence,
                    "forced_overrides": overrides,
                    "reference": baseline,
                    "python": py,
                    "rust": rs,
                }
            )
            if not overrides:
                genuine_traces.extend(baseline["calls"])
    hardware = any(
        c["function"] == "capability" and "result" in c for c in genuine_traces
    )
    system = platform.system()
    report["hardware"] = {
        "nvidia_detected": hardware,
        "driver_calls": genuine_traces,
        "forced_values_are_not_hardware_evidence": True,
    }
    report["mismatches"] = mismatches
    report["outputs_match"] = not mismatches
    report["hardware_validation"] = (
        "passed"
        if hardware and not mismatches and system in ("Linux", "Windows")
        else "failed"
        if mismatches
        else "not_validated"
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        f"{args.output}: outputs_match={report['outputs_match']}; hardware_validation={report['hardware_validation']}"
    )
    if mismatches or (
        report["hardware_validation"] != "passed" and not args.allow_no_hardware
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
