import base64
import copy
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from jsonschema import Draft202012Validator

from variant_provider import Provider
from variant_provider.definition import (
    PAYLOAD_TYPE,
    DefinitionError,
    load_definition,
    pae,
    strict_json,
    validate_definition,
    verify_envelope,
)
from variant_provider.engine import ReplayTransport

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def definition():
    return strict_json((ROOT / "json/tests/definitions/example.json").read_bytes())


@pytest.mark.parametrize(
    "payload",
    [
        b'{"a":1,"a":2}',
        b'{"nested":{"a":1,"a":2}}',
        b'{"v":NaN}',
        b"[" * 65 + b"0" + b"]" * 65,
    ],
)
def test_strict_json(payload):
    with pytest.raises(DefinitionError):
        strict_json(payload)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d.update(format_version="2.0.0"),
        lambda d: d.update(unexpected=True),
        lambda d: d["queries"]["all"].update(expr={"op": "eval", "source": "print(1)"}),
        lambda d: d["queries"]["all"].update(expr={"op": "query", "name": "missing"}),
        lambda d: d["queries"]["all"].update(expr={"op": "query", "name": "all"}),
        lambda d: d["queries"]["all"].update(expr={"op": "ref", "name": "missing"}),
        lambda d: d["constants"].update(forged={"$handle": "1"}),
        lambda d: d["features"].append(copy.deepcopy(d["features"][0])),
    ],
)
def test_invalid_definitions_fail_before_execution(definition, mutation):
    mutation(definition)
    with pytest.raises(DefinitionError):
        Provider(definition, transport=ReplayTransport([]))


def test_schemas_validate_against_meta_schema():
    for path in (ROOT / "json/schemas").glob("*.json"):
        Draft202012Validator.check_schema(strict_json(path.read_bytes()))


def test_offline_schema_selection(definition):
    definition["$schema"] = "https://invalid.invalid/evil.json"
    with pytest.raises(DefinitionError):
        validate_definition(definition)


def test_isolated_caches_and_json_only_update(definition):
    a = Provider(definition, environment={})
    changed = copy.deepcopy(definition)
    changed["data_version"] = 2
    changed["constants"]["values"] = ["ultra", "safe"]
    b = Provider(changed, environment={"EXAMPLE_FAST": "1"})
    assert a.evaluate("get_all_configs")[0]["values"] == ["fast", "portable"]
    assert b.evaluate("get_all_configs")[0]["values"] == ["ultra", "safe"]
    assert a.evaluate("get_supported_configs")[0]["values"] == ["portable"]
    assert b.evaluate("get_supported_configs")[0]["values"] == ["ultra", "safe"]


def test_live_environment_is_lazy_and_cached(definition, monkeypatch):
    monkeypatch.delenv("EXAMPLE_FAST", raising=False)
    p = Provider(definition)
    monkeypatch.setenv("EXAMPLE_FAST", "1")
    assert p.evaluate("get_supported_configs")[0]["values"] == ["fast", "portable"]
    monkeypatch.delenv("EXAMPLE_FAST")
    assert p.evaluate("get_supported_configs")[0]["values"] == ["fast", "portable"]
    p.clear_cache()
    assert p.evaluate("get_supported_configs")[0]["values"] == ["portable"]


def test_bounded_range(definition):
    definition["queries"]["probe"] = {
        "cache": False,
        "expr": {
            "op": "range",
            "start": {"op": "literal", "value": 0},
            "stop": {"op": "literal", "value": 100001},
            "step": {"op": "literal", "value": 1},
        },
    }
    definition["exports"]["probe"] = "probe"
    p = Provider(definition)
    assert p.invoke("probe")["error"]["category"] == "ResourceError"


def test_replay_rejects_extra_and_missing_calls():
    p = ReplayTransport([])
    with pytest.raises(Exception, match="Unexpected call"):
        p.call("unknown", [])
    with pytest.raises(Exception, match="Unconsumed"):
        ReplayTransport([{"function": "x", "args": [], "result": {}}]).assert_consumed()


def signed(definition):
    payload = json.dumps(definition).encode()
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    trust = [
        {
            "provider": "example",
            "namespace": "example",
            "public_key": base64.b64encode(public).decode(),
        }
    ]
    envelope = {
        "payloadType": PAYLOAD_TYPE,
        "payload": base64.b64encode(payload).decode(),
        "signatures": [{"sig": base64.b64encode(key.sign(pae(payload))).decode()}],
    }
    return payload, envelope, trust


def test_bundled_signature_matches_definition_bytes():
    root = ROOT / "json"
    trust = strict_json((root / "signatures/development-trust.json").read_bytes())
    payload = verify_envelope(
        (root / "signatures/nvidia.dsse.json").read_bytes(), trust
    )
    assert payload == (root / "nvidia.json").read_bytes()


def test_signature_roundtrip_and_scope(definition):
    payload, envelope, trust = signed(definition)
    assert verify_envelope(json.dumps(envelope).encode(), trust) == payload
    trust[0]["namespace"] = "another"
    with pytest.raises(DefinitionError):
        verify_envelope(json.dumps(envelope).encode(), trust)


@pytest.mark.parametrize(
    "change",
    ["payload", "payloadType", "sig", "minimum_version", "digest", "unknown_key"],
)
def test_signature_rejects_tampering(definition, change):
    payload, envelope, trust = signed(definition)
    if change == "payload":
        envelope["payload"] = base64.b64encode(payload + b" ").decode()
    elif change == "payloadType":
        envelope["payloadType"] = "application/json"
    elif change == "sig":
        envelope["signatures"][0]["sig"] = base64.b64encode(bytes(64)).decode()
    elif change == "minimum_version":
        trust[0]["minimum_data_version"] = 2
    elif change == "digest":
        trust[0]["sha256"] = "0" * 64
    else:
        trust = []
    with pytest.raises(DefinitionError):
        verify_envelope(json.dumps(envelope).encode(), trust)


def test_external_requires_signature_and_invalid_never_falls_back(tmp_path, definition):
    payload, envelope, trust = signed(definition)
    path = tmp_path / "definition.json"
    path.write_bytes(payload)
    signature = tmp_path / "sig.json"
    signature.write_text(json.dumps(envelope))
    with pytest.raises(DefinitionError):
        load_definition(path)
    load_definition(path, signature=signature, trust=trust)
    path.write_bytes(payload + b" ")
    with pytest.raises(DefinitionError):
        load_definition(path, signature=signature, trust=trust, allow_unsigned=True)
    load_definition(path, allow_unsigned=True)


def test_legacy_adapter_types(monkeypatch):
    from nvidia_variant_provider.plugin import NvidiaVariantPlugin
    from packaging.version import Version

    monkeypatch.setenv("NV_VARIANT_PROVIDER_FORCE_CUDA_DRIVER_VERSION", "12.7")
    monkeypatch.setenv("NV_VARIANT_PROVIDER_FORCE_SM_ARCH", "8.9")
    NvidiaVariantPlugin.clear_cache()
    assert NvidiaVariantPlugin.umd_version() == Version("12.7")
    assert NvidiaVariantPlugin.get_sm_architectures() == (8, 9)
    assert (
        NvidiaVariantPlugin.get_supported_configs()[0].name
        == "cuda_version_lower_bound"
    )


def probe(definition, expr):
    definition["queries"]["probe"] = {"cache": False, "expr": expr}
    definition["exports"]["probe"] = "probe"
    return Provider(definition).invoke("probe")


def test_format_values_are_literal(definition):
    result = probe(
        definition,
        {
            "op": "format",
            "template": "{a}/{b}",
            "args": {
                "a": {"op": "literal", "value": "{b}"},
                "b": {"op": "literal", "value": "end"},
            },
        },
    )
    assert result["value"] == "{b}/end"


def test_stable_descending_version_sort(definition):
    result = probe(
        definition,
        {
            "op": "sort",
            "reverse": True,
            "value": {
                "op": "list",
                "items": [
                    {"op": "parse_version", "value": {"op": "literal", "value": s}}
                    for s in ("12.0", "12", "13")
                ],
            },
        },
    )
    assert result["value"] == [
        {"$version": "13"},
        {"$version": "12.0"},
        {"$version": "12"},
    ]


def test_invalid_types_and_implicit_cycles(definition):
    definition["queries"]["all"]["expr"] = {"op": "configs", "mode": "all"}
    with pytest.raises(DefinitionError, match="Cyclic"):
        validate_definition(definition)


def test_query_graph_depth_bound(definition):
    for i in range(70):
        definition["queries"][f"q{i}"] = {
            "cache": False,
            "expr": {"op": "query", "name": f"q{i + 1}"}
            if i < 69
            else {"op": "literal", "value": 1},
        }
    with pytest.raises(DefinitionError, match="depth"):
        validate_definition(definition)


def test_unsigned_numeric_overflow_rejected():
    with pytest.raises(DefinitionError):
        strict_json(b'{"x":18446744073709551615}')


def test_definition_is_immutable_after_validation(definition):
    p = Provider(definition)
    definition["exports"]["get_all_configs"] = "evil"
    exposed = p.definition
    exposed["exports"]["get_all_configs"] = "evil"
    assert p.invoke("get_all_configs").get("error") is None


def test_record_and_format_keys_can_be_op(definition):
    expression = {
        "op": "record",
        "fields": {
            "op": {
                "op": "format",
                "template": "{op}",
                "args": {"op": {"op": "literal", "value": "ordinary"}},
            }
        },
    }
    assert probe(definition, expression)["value"] == {"op": "ordinary"}
