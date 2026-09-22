"""Standalone worker: imports only the oracle and the standard library.

Replay patches the I/O boundary in memory, never the reference source or rules.
Live mode wraps that boundary to capture a reproducible driver call transcript.
"""

import argparse
import dataclasses
import importlib
import json
import os
import sys
import warnings
from pathlib import Path


def load_symbol(value):
    module, name = value.split(":")
    return getattr(importlib.import_module(module), name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--bindings", type=Path, required=True)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    sys.path.insert(0, str(args.reference.resolve()))
    bindings = json.loads(args.bindings.read_text())
    request = json.loads(args.request.read_text())
    cls = load_symbol(bindings["provider"])
    environment_type = load_symbol(bindings["environment"])
    native = importlib.import_module(bindings["module"])
    from packaging.version import Version

    calls = request.get("calls", [])
    cursor = 0
    trace = []
    handles = {}

    def wrap(binding, original):
        def function(*pos, **kw):
            nonlocal cursor
            inputs = [
                pos[i] if i < len(pos) else kw[n]
                for i, n in enumerate(binding["inputs"])
            ]
            wire_inputs = list(inputs)
            if binding.get("handle_input"):
                if args.live:
                    token = next(k for k, v in handles.items() if v is inputs[0])
                    wire_inputs[0] = {"$handle": token}
                else:
                    wire_inputs[0] = inputs[0]
            wire_inputs += binding["implicit"]
            record = {"function": binding["function"], "args": wire_inputs}
            if args.live:
                try:
                    result = original(*pos, **kw)
                except native.NVMLError as exc:
                    record["error"] = {
                        "category": "NativeError",
                        "message": "Native call failed",
                        "code": exc.value,
                    }
                    trace.append(record)
                    raise
                if binding.get("handle_output"):
                    token = str(len(handles) + 1)
                    handles[token] = result
                    output = {"value": {"$handle": token}}
                elif len(binding["outputs"]) == 1:
                    output = {binding["outputs"][0]: result}
                else:
                    output = dict(zip(binding["outputs"], result or []))
                record["result"] = output
                trace.append(record)
                return result
            if cursor >= len(calls):
                raise AssertionError(f"Unexpected reference call: {record}")
            expected = calls[cursor]
            cursor += 1
            assert record == {k: expected[k] for k in ("function", "args")}, (
                record,
                expected,
            )
            if "error" in expected:
                assert expected["error"]["category"] == "NativeError"
                raise native.NVMLError(expected["error"].get("code", 999))
            output = expected["result"]
            if len(binding["outputs"]) == 1:
                return output[binding["outputs"][0]]
            return (
                tuple(output[k] for k in binding["outputs"])
                if binding["outputs"]
                else None
            )

        return function

    for binding in bindings["bindings"]:
        setattr(
            native,
            binding["attribute"],
            wrap(binding, getattr(native, binding["attribute"])),
        )

    def wire(v):
        if isinstance(v, Version):
            return {"$version": str(v)}
        if dataclasses.is_dataclass(v):
            return {k: wire(x) for k, x in dataclasses.asdict(v).items()}
        if isinstance(v, (list, tuple)):
            return [wire(x) for x in v]
        return v

    def clear():
        for name in dir(cls):
            method = getattr(cls, name)
            if hasattr(method, "cache_clear"):
                method.cache_clear()
        environment_type.from_system.cache_clear()

    # Only fixture-owned variables are changed; live callers provide a clean env.
    controlled = set(request.get("environment", {}))
    if not args.live:
        controlled.update(
            (
                "NV_VARIANT_PROVIDER_FORCE_CUDA_DRIVER_VERSION",
                "NV_VARIANT_PROVIDER_FORCE_SM_ARCH",
            )
        )
    for key in controlled:
        os.environ.pop(key, None)
    os.environ.update(request.get("environment", {}))
    results = []
    for step in request["steps"]:
        if "environment" in step:
            for key in controlled:
                os.environ.pop(key, None)
            controlled.update(step["environment"])
            os.environ.update(step["environment"])
        if step.get("clear_cache"):
            clear()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            try:
                result = {"value": wire(getattr(cls, step["method"])())}
            except Exception as exc:  # noqa: BLE001
                # Every provider exception is an oracle result for parity comparison.
                result = {
                    "error": {"category": type(exc).__name__, "message": str(exc)}
                }
            result["warnings"] = [
                {"category": type(w.message).__name__, "message": str(w.message)}
                for w in caught
            ]
        results.append(result)
    if not args.live:
        assert cursor == len(calls), (
            f"Unconsumed reference calls: {len(calls) - cursor}"
        )
    print(
        json.dumps(
            {
                "results": results,
                "calls": trace,
                "metadata": {
                    "namespace": cls.namespace,
                    "is_build_plugin": cls.is_build_plugin,
                },
            }
        )
    )


if __name__ == "__main__":
    main()
