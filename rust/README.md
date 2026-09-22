# Variant provider (Rust)

An independent interpreter for versioned, signed JSON provider definitions.

```rust
let mut provider = variant_provider::Provider::bundled()?;
let configs = provider.evaluate("get_supported_configs")?;
# Ok::<(), Box<dyn std::error::Error>>(())
```

Definitions with native function declarations must be trusted. See the workspace
`docs/security.md` for the trust model and `docs/format.md` for the language.

Publish to crates.io using Cargo, from this directory:

```sh
cargo package --locked
cargo publish --locked
```

The `data/` symlinks point to the canonical definitions in `../json/`. Cargo
includes their contents as regular files in the published crate. Release
checkouts must preserve symlinks (on Windows, enable Git's `core.symlinks`).
Consumers of the published crate do not need symlink support or Python.
