"""Shared provider fixtures; no vendor behavior in the runner."""

from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry

from .definition import bundled_bytes, load_definition, strict_json
from .engine import Provider, ReplayTransport


def read_case(path):
    case = strict_json(Path(path).read_bytes())
    schema = strict_json(bundled_bytes("schemas/case-1.0.0.schema.json"))
    Draft202012Validator(schema, registry=Registry()).validate(case)
    return case


def run_case(path, check=True):
    path = Path(path)
    case = read_case(path)
    root = path.parent.parent.parent
    definition, _ = load_definition(root / case["definition"], allow_unsigned=True)
    transport = ReplayTransport(case["calls"])
    environment = dict(case["environment"])
    provider = Provider(definition, transport=transport, environment=environment)
    results = []
    for step in case["steps"]:
        if "environment" in step:
            environment.clear()
            environment.update(step["environment"])
        if step.get("clear_cache", False):
            provider.clear_cache()
        result = provider.invoke(step["method"])
        if check:
            assert result == step["expected"], (
                f"{case['id']} / {step['method']}: {result} != {step['expected']}"
            )
        results.append(result)
    transport.assert_consumed()
    return results


def inventory(directory):
    paths = sorted(Path(directory).glob("*.json"))
    if not paths:
        raise ValueError("No shared cases discovered")
    for path in paths:
        read_case(path)
    return paths
