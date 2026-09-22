"""Read-only validation: never reformat signed definition bytes."""

import argparse
from pathlib import Path

from jsonschema import Draft202012Validator
from variant_provider.definition import (
    SCHEMA_ID,
    strict_json,
    validate_definition,
    verify_envelope,
)
from variant_provider.fixtures import read_case

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="*", type=Path)
    args = parser.parse_args()
    for path in args.files or sorted((ROOT / "json").rglob("*.json")):
        document = strict_json(path.read_bytes())
        if path.parent.name == "schemas":
            Draft202012Validator.check_schema(document)
        elif isinstance(document, dict) and document.get("$schema") == SCHEMA_ID:
            validate_definition(document)
        elif path.parent.name == "cases":
            read_case(path)
    trust = strict_json((ROOT / "json/signatures/development-trust.json").read_bytes())
    payload = verify_envelope(
        (ROOT / "json/signatures/nvidia.dsse.json").read_bytes(), trust
    )
    if payload != (ROOT / "json/nvidia.json").read_bytes():
        raise ValueError(
            "NVIDIA definition differs from the signed payload; re-sign it"
        )


if __name__ == "__main__":
    main()
