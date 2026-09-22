from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .definition import (
    PAYLOAD_TYPE,
    bundled_bytes,
    load_definition,
    pae,
    strict_json,
    validate_definition,
)
from .engine import Provider
from .fixtures import inventory, run_case


def main():
    parser = argparse.ArgumentParser(description="JSON-defined variant provider")
    parser.add_argument(
        "command",
        choices=["validate", "invoke", "replay", "inventory", "keygen", "sign"],
    )
    parser.add_argument("--definition", type=Path)
    parser.add_argument("--signature", type=Path)
    parser.add_argument("--trust", type=Path)
    parser.add_argument("--allow-unsigned", action="store_true")
    parser.add_argument("--method", default="get_supported_configs")
    parser.add_argument("--case", type=Path)
    parser.add_argument("--cases", type=Path)
    parser.add_argument("--key", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--provider", default="nvidia")
    parser.add_argument("--namespace", default="nvidia")
    args = parser.parse_args()
    try:
        if args.command == "keygen":
            if args.key is None or args.output is None:
                parser.error("keygen requires --key and --output (trust file)")
            key = Ed25519PrivateKey.generate()
            raw = key.private_bytes(
                serialization.Encoding.Raw,
                serialization.PrivateFormat.Raw,
                serialization.NoEncryption(),
            )
            args.key.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(args.key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(raw)
            public = key.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw
            )
            with args.output.open("x", encoding="utf-8") as f:
                json.dump(
                    [
                        {
                            "provider": args.provider,
                            "namespace": args.namespace,
                            "public_key": base64.b64encode(public).decode(),
                            "minimum_data_version": 1,
                        }
                    ],
                    f,
                    indent=2,
                )
                f.write("\n")
            return
        if args.command == "sign":
            if args.key is None or args.definition is None or args.output is None:
                parser.error("sign requires --key, --definition, and --output")
            payload = args.definition.read_bytes()
            validate_definition(strict_json(payload))
            key = Ed25519PrivateKey.from_private_bytes(args.key.read_bytes())
            envelope = {
                "payloadType": PAYLOAD_TYPE,
                "payload": base64.b64encode(payload).decode(),
                "signatures": [
                    {"sig": base64.b64encode(key.sign(pae(payload))).decode()}
                ],
            }
            args.output.write_text(
                json.dumps(envelope, indent=2) + "\n", encoding="utf-8"
            )
            return
        if args.command == "inventory":
            if args.cases is None:
                parser.error("inventory requires --cases")
            print(json.dumps({"cases": [p.name for p in inventory(args.cases)]}))
            return
        if args.command == "replay":
            if args.case is None:
                parser.error("replay requires --case")
            print(json.dumps({"results": run_case(args.case, check=False)}))
            return
        trust = strict_json(args.trust.read_bytes()) if args.trust else []
        definition, digest = load_definition(
            args.definition,
            signature=args.signature,
            trust=trust,
            allow_unsigned=args.allow_unsigned,
        )
        if args.command == "validate":
            print(
                json.dumps(
                    {
                        "valid": True,
                        "provider": definition["provider"],
                        "format_version": definition["format_version"],
                        "data_version": definition["data_version"],
                        "definition_sha256": digest,
                        "schema_sha256": hashlib.sha256(
                            bundled_bytes("schemas/provider-1.0.0.schema.json")
                        ).hexdigest(),
                    }
                )
            )
        else:
            provider = Provider(definition)
            print(
                json.dumps(
                    {"results": [provider.invoke(m) for m in args.method.split(",")]}
                )
            )
    except (ValueError, OSError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
