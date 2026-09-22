"""Offline schema validation, semantic checks, and definition trust."""

from __future__ import annotations

import base64
import hashlib
import json
from functools import lru_cache
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from jsonschema import Draft202012Validator
from referencing import Registry

SCHEMA_ID = "urn:variant-provider:schema:definition:1.0.0"
PAYLOAD_TYPE = "application/vnd.variant-provider.definition.v1+json"
MAX_BYTES = 4 * 1024 * 1024
MAX_DEPTH = 64


class DefinitionError(ValueError):
    """A definition cannot be trusted or executed."""


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DefinitionError(f"Duplicate key: {key}")
        result[key] = value
    return result


def strict_json(data: bytes):
    if len(data) > MAX_BYTES:
        raise DefinitionError("JSON exceeds 4 MiB limit")
    try:
        obj = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(
                DefinitionError("Non-finite number")
            ),
        )
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise DefinitionError(str(exc)) from exc

    def depth(value, n=0):
        if n > MAX_DEPTH:
            raise DefinitionError("JSON exceeds nesting limit")
        if type(value) is float:
            raise DefinitionError("Only integer JSON numbers are supported")
        if type(value) is int and not -(2**63) <= value < 2**63:
            raise DefinitionError("JSON integer out of range")
        if isinstance(value, dict):
            for item in value.values():
                depth(item, n + 1)
        elif isinstance(value, list):
            for item in value:
                depth(item, n + 1)

    depth(obj)
    return obj


def data_dir():
    installed = Path(__file__).parent / "_data"
    if installed.is_dir():
        return installed
    return Path(__file__).resolve().parents[3] / "json"


def bundled_bytes(name):
    root = data_dir()
    payload = (root / name).read_bytes()
    manifest_path = root / "manifest.json"
    if manifest_path.exists():
        manifest = strict_json(manifest_path.read_bytes())
        if manifest.get(name) != hashlib.sha256(payload).hexdigest():
            raise DefinitionError(f"Bundled resource digest mismatch: {name}")
    return payload


@lru_cache(maxsize=1)
def _validator():
    schema = strict_json(bundled_bytes("schemas/provider-1.0.0.schema.json"))
    return Draft202012Validator(schema, registry=Registry())


def validate_definition(obj):
    validator = _validator()
    errors = sorted(
        validator.iter_errors(obj), key=lambda e: str(list(e.absolute_path))
    )
    if errors:
        err = errors[0]
        raise DefinitionError(
            f"Schema error at /{'/'.join(map(str, err.absolute_path))}: {err.message}"
        )
    queries = obj["queries"]
    edges = {name: set() for name in queries}

    def walk(expr, scope, query):
        op = expr["op"]
        if op == "ref":
            if expr["name"] not in scope:
                raise DefinitionError(f"Unknown local reference: {expr['name']}")
        elif op == "query":
            name = expr["name"]
            if name not in queries:
                raise DefinitionError(f"Unknown query: {name}")
            edges[query].add(name)
        elif op == "const":
            if expr["name"] not in obj["constants"]:
                raise DefinitionError(f"Unknown constant: {expr['name']}")
        elif op == "configs":
            for feature in obj["features"]:
                edges[query].add(feature[expr["mode"]])
        elif op == "call":
            if expr["function"] not in obj["functions"]:
                raise DefinitionError(f"Unknown function: {expr['function']}")
            fn = obj["functions"][expr["function"]]
            if len(expr["args"]) != sum(a["direction"] == "in" for a in fn["args"]):
                raise DefinitionError("Native argument count mismatch")
        elif op in ("warn", "raise") and expr["diagnostic"] not in obj["diagnostics"]:
            raise DefinitionError("Unknown diagnostic")
        if op == "let":
            local = set(scope)
            for binding in expr["bindings"]:
                if binding["name"] in local:
                    raise DefinitionError("Duplicate local binding")
                walk(binding["value"], local, query)
                local.add(binding["name"])
            walk(expr["body"], local, query)
            return
        if op in ("map", "filter"):
            walk(expr["items"], scope, query)
            walk(expr["body"], scope | {expr["var"]}, query)
            return
        for key, value in expr.items():
            if key == "value" and op == "literal":
                continue
            children(value, scope, query)

    def children(value, scope, query):
        if isinstance(value, dict):
            if isinstance(value.get("op"), str):
                walk(value, scope, query)
            else:
                for item in value.values():
                    children(item, scope, query)
        elif isinstance(value, list):
            for item in value:
                children(item, scope, query)

    for name, q in queries.items():
        walk(q["expr"], set(), name)
    visiting, visited = set(), set()

    def visit(name):
        if name in visiting:
            raise DefinitionError("Cyclic query dependency")
        if name in visited:
            return
        if len(visiting) >= MAX_DEPTH:
            raise DefinitionError("Query dependency depth exceeded")
        visiting.add(name)
        if name not in edges:
            raise DefinitionError(f"Unknown query: {name}")
        for child in edges[name]:
            visit(child)
        visiting.remove(name)
        visited.add(name)

    for name in queries:
        visit(name)
    depths = {}

    def query_depth(name):
        if name not in depths:
            depths[name] = expression_depth(queries[name]["expr"])
        return depths[name]

    def expression_depth(expr):
        if expr["op"] == "literal":
            return 1
        if expr["op"] == "query":
            return 1 + query_depth(expr["name"])
        if expr["op"] == "configs":
            return 1 + max(
                (query_depth(f[expr["mode"]]) for f in obj["features"]), default=0
            )

        def children(value):
            if isinstance(value, dict):
                if isinstance(value.get("op"), str):
                    return expression_depth(value)
                return max((children(x) for x in value.values()), default=0)
            if isinstance(value, list):
                return max((children(x) for x in value), default=0)
            return 0

        return 1 + max((children(v) for v in expr.values()), default=0)

    for name in queries:
        if query_depth(name) > MAX_DEPTH:
            raise DefinitionError("Expanded expression depth exceeded")
    for name in obj["exports"].values():
        if name not in queries:
            raise DefinitionError(f"Unknown exported query: {name}")
    for fn in obj["functions"].values():
        if fn["library"] not in obj["libraries"]:
            raise DefinitionError("Unknown library")
        names = [a["name"] for a in fn["args"]]
        if len(names) != len(set(names)):
            raise DefinitionError("Duplicate native argument name")
        for arg in fn["args"]:
            if arg["type"] == "buffer" and (
                arg["direction"] != "out" or not 1 <= arg.get("size", 0) <= 1048576
            ):
                raise DefinitionError("Invalid output buffer")
    names = [f["name"] for f in obj["features"]]
    if len(names) != len(set(names)):
        raise DefinitionError("Duplicate feature")
    for feature in obj["features"]:
        for key in ("all", "supported"):
            if feature[key] not in queries:
                raise DefinitionError("Unknown feature query")

    # Reserved tagged objects are engine values, never injectable input data.
    def reserved(value):
        if isinstance(value, dict):
            if any(k.startswith("$") for k in value):
                raise DefinitionError("Reserved value tag")
            for x in value.values():
                reserved(x)
        elif isinstance(value, list):
            for x in value:
                reserved(x)

    reserved(obj["constants"])

    def literals(value):
        if isinstance(value, dict):
            if value.get("op") == "literal":
                reserved(value["value"])
            else:
                for x in value.values():
                    literals(x)
        elif isinstance(value, list):
            for x in value:
                literals(x)

    literals(queries)
    from .typecheck import validate_types

    validate_types(obj, DefinitionError)
    return obj


def pae(payload):
    typ = PAYLOAD_TYPE.encode()
    return (
        b"DSSEv1 "
        + str(len(typ)).encode()
        + b" "
        + typ
        + b" "
        + str(len(payload)).encode()
        + b" "
        + payload
    )


def verify_envelope(data, trust):
    envelope = strict_json(data)
    if not isinstance(envelope, dict) or set(envelope) != {
        "payloadType",
        "payload",
        "signatures",
    }:
        raise DefinitionError("Invalid signature envelope")
    if envelope["payloadType"] != PAYLOAD_TYPE:
        raise DefinitionError("Unexpected signature payload type")
    try:
        payload = base64.b64decode(envelope["payload"], altchars=b"-_", validate=True)
        accepted = []
        for sig in envelope["signatures"]:
            if not isinstance(sig, dict) or set(sig) - {"keyid", "sig"}:
                raise DefinitionError("Invalid signature record")
            raw = base64.b64decode(sig["sig"], altchars=b"-_", validate=True)
            for entry in trust:
                try:
                    key = Ed25519PublicKey.from_public_bytes(
                        base64.b64decode(entry["public_key"], validate=True)
                    )
                    key.verify(raw, pae(payload))
                    accepted.append(entry)
                except InvalidSignature:
                    pass
        if not accepted:
            raise DefinitionError("No trusted valid signature")
    except (ValueError, TypeError, KeyError) as exc:
        raise DefinitionError(f"Invalid signature: {exc}") from exc
    obj = strict_json(payload)
    if not isinstance(obj, dict):
        raise DefinitionError("Definition must be an object")
    identity = obj.get("provider", {})
    if not isinstance(identity, dict):
        raise DefinitionError("Provider identity must be an object")
    if not any(
        e.get("provider") == identity.get("id")
        and e.get("namespace") == identity.get("namespace")
        and isinstance(obj.get("data_version"), int)
        and obj["data_version"] >= e.get("minimum_data_version", 1)
        and (not e.get("sha256") or e["sha256"] == hashlib.sha256(payload).hexdigest())
        for e in accepted
    ):
        raise DefinitionError("Signature trust scope or data version mismatch")
    return payload


def load_definition(path=None, *, signature=None, trust=(), allow_unsigned=False):
    if path is None:
        payload = bundled_bytes("nvidia.json")
    else:
        payload = Path(path).read_bytes()
        if len(payload) > MAX_BYTES:
            raise DefinitionError("Definition exceeds size limit")
        if signature is not None:
            verified = verify_envelope(Path(signature).read_bytes(), trust)
            if payload != verified:
                raise DefinitionError("Signed payload differs from definition bytes")
        elif not allow_unsigned:
            raise DefinitionError("External definitions require a trusted signature")
    obj = validate_definition(strict_json(payload))
    return obj, hashlib.sha256(payload).hexdigest()
