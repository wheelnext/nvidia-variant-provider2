# Packaging and migration

The Python engine distribution is `variant-provider`. A separate thin
distribution, `nvidia-variant-provider`, preserves the old import and
`variant_plugins` entry point and depends on the engine. The Rust crate is
`variant-provider`; its binary is `variant-provider-rs`. The Python binary is
`variant-provider-py`.

Existing consumers can continue using:

```python
from nvidia_variant_provider.plugin import NvidiaVariantPlugin
configs = NvidiaVariantPlugin.get_supported_configs()
```

Generic consumers select a definition through `Provider`. Do not install the old
reference wheel and new compatibility wheel into one environment. The validation
worker runs the reference separately to avoid this collision and its vendored
`packaging` import behavior.

## Artifacts

```sh
uv run python tools/package.py
```

The command builds engine and compatibility wheels/sdists into
`artifacts/dist/`. It does not publish. Both Python packages use Flit configured
in `pyproject.toml`, with no `setup.py` or custom build hooks. The packaging
tool stages the engine source, canonical JSON, and generated digest manifest in
a temporary directory before invoking Flit. Use this command for release
artifacts so they include the shared resources. Packaged source builds use
staged resources rather than repository-relative files.

The Rust crate uses Cargo directly:

```sh
cargo package --locked --manifest-path rust/Cargo.toml
cargo publish --locked --manifest-path rust/Cargo.toml
```

Cargo follows the crate's `data/` symlinks to the canonical JSON and includes
regular files in the crate archive. Release checkouts must preserve symlinks; on
Windows this requires Git's `core.symlinks`. Published crates build without
Python or the repository. There is no custom Rust packaging or installer tool.

Development builds read the canonical `json/` files directly. Generated package
copies are not independently maintained source files. Both runtime validation
CLIs print exact resource digests for artifact comparison. Signing keys,
development trust, fixtures, reference code, and native test libraries are not
runtime package resources.

An external signed JSON update can change behavior without rebuilding either
engine. Updating the bundled default requires repackaging its data; no backend
source changes are needed. Existing provider instances intentionally retain
their validated definition and caches. Construct a new instance after a data
update.

## Rollout gates

1. Both language suites and shared CLI comparison pass.
2. Wheels, sdists, and the crate build and run independently of the checkout.
3. Invalid signatures and definitions fail before probing.
4. Linux and Windows NVIDIA comparison reports both pass.
5. Provision production trust before treating a release as production-signed.

Keep `reference-design/` intact during rollout. A behavioral change after
migration is a separately reviewed JSON/data-version change, not an unannounced
correction inside a backend.
