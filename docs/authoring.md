# Authoring and JSON-only updates

Start from `json/tests/definitions/example.json` for environment-driven rules or
`native-example.json` for a local driver API with an opaque handle and 64-bit
output. Both run without vendor branches in either engine.

1. Assign a provider identifier and namespace; define constants.
2. Declare local library candidates, exact ABI signatures, and success codes.
3. Express detection as named queries with explicit fallback and cleanup.
4. Express value catalogs and compatibility rules with the generic operations.
5. Put feature priority in the `features` array and map public method names in
   `exports`. Make caching explicit for each query.
6. Add shared cases under `json/tests/cases/`, including errors and ordering.
7. Validate and run both test suites; increment the integer data version for a
   released change, sign exact bytes, and distribute the updated definition.

```sh
uv run python -m variant_provider.cli validate --definition json/nvidia.json --allow-unsigned
rust/target/debug/variant-provider-rs validate --definition json/nvidia.json --allow-unsigned
```

`--allow-unsigned` is an explicit development choice. Consumers of separately
distributed definitions use trusted signatures. Neither engine polls a server or
reloads an existing provider automatically. Construct a new provider to use a
new definition; this creates a new isolated cache.

Changing existing ranges, symbols with supported signatures, library locations,
feature names, preference order, compatibility rules, override names, and
messages requires no runtime source change. New ABI types, new engine
operations, CPU instruction probes, and shell-command probes are not expressible
in v1. Adding such a capability is an engine/format project, not a JSON-only
edit.

Do not conceal provider-specific policy in a new operator. A synthetic
definition must demonstrate any proposed generic operation. Version parsing is a
generic operation; CUDA integer decoding is ordinary division/modulo in NVIDIA
JSON.

Schemas and definitions are bundled together for offline use. Definitions cannot
supply alternate schemas or remote includes. The runtime handles the supported
schema identifier itself.
