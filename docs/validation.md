# Validation and hardware handoff

## Automated suites

CI runs in three phases:

1. Prek runs once on Linux with `uv run --frozen prek run -a`.
2. Four jobs test Python and Rust on Linux and Windows. Rust jobs also compare
   both CLIs against the shared JSON corpus.
3. After all four test jobs pass, Linux builds and verifies the Python packages
   and Rust crate. This phase does not publish.

Jobs install the uv development dependencies; Rust jobs install the Rust
toolchain, with rustfmt and Clippy for linting. Prek installs hook tools in
isolated environments, including the pinned cargo-audit dependency. Windows jobs
initialize the MSVC compiler environment for the native ABI tests.

```sh
uv sync --frozen
uv run pytest
cargo test --manifest-path rust/Cargo.toml
uv run python tools/check_corpus.py --rust-bin rust/target/debug/variant-provider-rs
```

On Windows use the `.exe` Rust binary; uv commands are the same. Native tests
require `cc` on Linux/macOS or `cl` in a Visual Studio developer shell on
Windows. Missing compilers fail the suite rather than silently skipping ABI
coverage.

Engine tests cover strict JSON, schemas, semantic validation, resource limits,
caches, signatures, and the compatibility facade. Shared behavior cases live
only in `json/tests/cases/`; their schema is validated by both runners. Every
case is discovered, every expected outcome is required, and native transcripts
must be fully consumed. The CLI cross-check compares discovered files and all
outcomes.

The native test shim in `json/tests/native/probe.c` implements both the relevant
NVML signatures and a synthetic API with opaque handles and 64-bit arguments.
Both native transports execute the same shared expectations. Replay tests cover
failure branches; native tests verify ABI marshalling independently of replay.

## Reference expectations

NVIDIA expectations were captured by the unmodified reference implementation.
The worker replaces only its NVML I/O functions in memory with scripted results;
it does not patch detection rules or provider methods. Reference wrapper
bindings are isolated in `json/tests/reference-bindings.json`, not runtime
engine code.

Explicit regeneration is available when intentionally updating the oracle:

```sh
uv run python json/tests/capture.py
```

Review expectation diffs. Ordinary test runs never regenerate expectations.
Cases record both oracle revisions. Synthetic-provider expectations are authored
independently because they have no NVIDIA reference equivalent.

## Required NVIDIA runs

Build the Rust binary and install Python as above on **both** a Linux NVIDIA
host and a Windows NVIDIA host. Keep drivers and hardware stable during the
comparison. The script clears inherited NVIDIA overrides for genuine-detection
scenarios and labels forced-override scenarios separately.

Linux, from the repository root:

```sh
uv run python -m variant_provider.validation.compare --rust-bin rust/target/debug/variant-provider-rs --output artifacts/linux-nvidia.json
```

Windows, from the repository root:

```powershell
uv run python -m variant_provider.validation.compare --rust-bin rust\target\debug\variant-provider-rs.exe --output artifacts\windows-nvidia.json
```

The harness checks frozen reference/dependency revisions and refuses tracked
oracle modifications. It compares all six methods individually, ordered/repeated
sequences, static metadata, valid overrides, range-check bypass, and malformed
input. Python and Rust definition/schema digests must agree. Array order, null,
errors, warnings, and categories are retained. Differences include JSON paths.

Reports record host and driver data, revisions, resource digests, complete
method outputs, forced inputs, native reference traces, and mismatches. A
passing hardware report requires a successful genuine capability query on Linux
or Windows and zero mismatches. An unavailable driver, empty GPU result, forced
values, or a Mac cannot satisfy that gate.

Return both report files. On mismatch, retain the report, create a focused
shared replay fixture, fix the responsible JSON rule or generic operation, and
rerun the affected hardware validation. Do not normalize differences away.

A local no-GPU smoke test is available with `--allow-no-hardware`. Its report
still says `hardware_validation: "not_validated"`; a successful exit is only
permission to finish the smoke test.
