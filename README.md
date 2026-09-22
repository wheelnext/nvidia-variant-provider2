# Declarative Variant Providers

Independent Python and Rust engines interpret the same provider definition in
[`json/nvidia.json`](json/nvidia.json). Driver calls, compatibility rules,
feature priority, values, overrides, and diagnostics are data. The original
implementation remains in `reference-design/` as the compatibility oracle.

```sh
uv sync --frozen
cargo build --manifest-path rust/Cargo.toml
uv run python -m variant_provider.cli invoke
rust/target/debug/variant-provider-rs invoke
```

On Windows append `.exe` to the Rust binary; uv commands are the same. The
engines query local drivers only; they never download definitions or libraries,
evaluate source strings, or execute shell instructions from JSON. Native
declarations nevertheless require trust: a signature authenticates an author,
not a function's ABI or its behavior.

## Tests

```sh
uv run pytest
cargo test --manifest-path rust/Cargo.toml
uv run python tools/check_corpus.py --rust-bin rust/target/debug/variant-provider-rs
```

The root project is a non-package uv workspace; `uv sync` installs both Python
workspace members and the development tools from the single root `uv.lock`. Ruff
and prek are root development dependencies. To enable and run Git hooks:

```sh
uv run prek install
uv run prek run --all-files
```

Both test runners discover every case in `json/tests/cases/`. Engine tests stay
in `python/tests/` and `rust/tests/`. Native tests need a C compiler. On Windows
run from a Visual Studio developer shell (`cl` must be available).

## Documentation

- [Architecture and interfaces](docs/architecture.md)
- [Definition language and schemas](docs/format.md)
- [Authoring and JSON-only updates](docs/authoring.md)
- [NVIDIA mapping and preserved behavior](docs/nvidia.md)
- [Signing and trust](docs/security.md)
- [Testing and NVIDIA hardware validation](docs/validation.md)
- [Packaging and migration](docs/migration.md)
- [Implementation status and limits](docs/status.md)

The local macOS comparison is a smoke test. **Linux and Windows NVIDIA hardware
validation are required before declaring the transition equivalent on
hardware.**
