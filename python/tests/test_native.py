import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from variant_provider import Provider
from variant_provider.definition import load_definition
from variant_provider.engine import NativeTransport, ProviderError

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    directory = tmp_path_factory.mktemp("native")
    source = ROOT / "json/tests/native/probe.c"
    if sys.platform == "win32":
        path = directory / "probe.dll"
        command = [
            "cl",
            "/nologo",
            "/LD",
            str(source),
            f"/Fe:{path}",
            f"/Fo:{directory}/",
            "/link",
            f"/IMPLIB:{directory}/probe.lib",
        ]
    else:
        path = directory / (
            "libprobe.dylib" if sys.platform == "darwin" else "libprobe.so"
        )
        command = [
            os.environ.get("CC", "cc"),
            "-dynamiclib" if sys.platform == "darwin" else "-shared",
            "-fPIC",
            str(source),
            "-o",
            str(path),
        ]
    subprocess.run(command, check=True, capture_output=True)
    return path


@pytest.mark.parametrize(
    "case_name,library_name",
    [("detected_12070", "management"), ("native_example", "probe")],
)
def test_actual_native_abi(library, case_name, library_name):
    case = json.loads((ROOT / f"json/tests/cases/{case_name}.json").read_text())
    definition, _ = load_definition(
        ROOT / "json" / case["definition"], allow_unsigned=True
    )
    transport = NativeTransport(
        definition, environment={}, library_overrides={library_name: library}
    )
    provider = Provider(definition, transport=transport, environment={})
    for step in case["steps"]:
        assert provider.invoke(step["method"]) == step["expected"]
    assert transport.trace == [
        {k: c[k] for k in ("function", "args")} for c in case["calls"]
    ]
    assert not transport.handles


def test_missing_library_error():
    definition, _ = load_definition()
    transport = NativeTransport(
        definition, library_overrides={"management": "/definitely/missing/library"}
    )
    with pytest.raises(ProviderError) as error:
        transport.call("init", [0])
    assert error.value.category == "NativeError"
    assert error.value.code == 12


def test_handles_cannot_cross_sessions(library):
    definition, _ = load_definition(
        ROOT / "json/tests/definitions/native-example.json", allow_unsigned=True
    )
    native = NativeTransport(definition, library_overrides={"probe": library})
    old = native.call("open", [])["handle"]
    native.call("close", [])
    fresh = native.call("open", [])["handle"]
    assert old != fresh
    with pytest.raises(ProviderError, match="Invalid native handle"):
        native.call("read", [old, 1])
    native.call("close", [])
