# Signing and trust

JSON is not executable source, but trusted FFI declarations can invoke native
code. The engine prohibits scripts, eval, shell commands, remote libraries,
callbacks, and arbitrary address literals. It cannot prove that a declared
symbol has the claimed ABI or that a trusted native library is safe. Schema
validity and a valid signature do not provide that guarantee.

## Loading policy

- Bundled definitions inherit installed-package trust. Python checks a packaged
  digest manifest; Rust checks the definition digest embedded at build time.
- External definitions require Ed25519 signatures unless a host explicitly opts
  into unsigned local development loading.
- Trusted public keys are host configuration. JSON cannot authorize its own key.
- A trust entry binds `public_key` (base64 raw Ed25519), `provider`, and
  `namespace`. Optional `minimum_data_version` and `sha256` enforce a floor or
  exact content pin.
- Verification/validation failure occurs before loading a native library. A
  present invalid signature never falls back to unsigned mode.
- There is no automatic update fetch, revocation check, rollback database, or
  telemetry. For rollback prevention, configure a version floor or digest pin.

Use an authenticated channel to deliver public trust entries. A public key
shipped next to an untrusted definition is not automatically trustworthy.
Removing a key from host trust revokes it for future loads. Multiple configured
keys allow rotation; one valid signature from a correctly scoped trusted key is
required.

The DSSE envelope uses payload type
`application/vnd.variant-provider.definition.v1+json`, the standard DSSE
pre-authentication encoding, and exact UTF-8 definition bytes. The envelope
contains the payload and signatures. When a separate JSON file is supplied, its
bytes must equal the verified payload. Whitespace changes therefore require
re-signing. No JSON canonicalization is performed. `keyid` is only an optional
hint; it never authorizes a key. See the
[DSSE protocol](https://github.com/secure-systems-lab/dsse/blob/master/protocol.md).

## Local demonstration

This checkout includes `json/signatures/nvidia.dsse.json` and a development-only
public trust file. A private key was generated under ignored `.local/`; it is
not committed or bundled in distributions. That key is a local demonstration,
not production trust. Neither engine trusts the development public key by
default.

```sh
uv run python -m variant_provider.cli validate --definition json/nvidia.json --signature json/signatures/nvidia.dsse.json --trust json/signatures/development-trust.json
rust/target/debug/variant-provider-rs validate --definition json/nvidia.json --signature json/signatures/nvidia.dsse.json --trust json/signatures/development-trust.json
```

Generate a fresh local key with new output paths:

```sh
uv run python -m variant_provider.cli keygen --key .local/my-key.ed25519 --output .local/my-trust.json
uv run python -m variant_provider.cli sign --key .local/my-key.ed25519 --definition json/nvidia.json --output .local/nvidia.dsse.json
```

The private-key command uses exclusive creation and restrictive file
permissions. For production, provision a release signing key through your
existing secret management system, distribute trust separately, and keep key
material outside source and artifacts. The local example does not claim
production provenance.

## Native boundary and limits

Native argument types, buffer sizes, reference graphs, local value types, and
handle ownership are checked. Handles are engine-owned and cannot be public
export values. Each invocation serializes native session access. The host can
inject local library paths for native tests; JSON cannot select that facility.
Library names retain normal OS loader behavior, including relevant OS loader
configuration. This is a trusted-plugin model, not a native-code sandbox.

Native calls cannot be interrupted safely in-process. The hardware comparison
harness uses separate processes with timeouts; the runtime itself does not
promise crash containment or a hard native-call deadline.
