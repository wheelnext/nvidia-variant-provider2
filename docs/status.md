# Implementation status

Implemented: independent Python/Rust interpreters, offline schemas and semantic
validation, NVIDIA JSON, synthetic providers, native/replay transports, legacy
Python adapter, matching CLIs, local Ed25519/DSSE signing, shared behavior
cases, native ABI tests, packaging, CI, and the hardware comparison harness.

Local macOS validation is a development check. The comparison report correctly
marks NVIDIA hardware validation as `not_validated`. Linux and Windows native
mock tests are configured in CI; their execution and real NVIDIA reports are not
claimed by a local macOS run.

Required external acceptance remains **Linux NVIDIA and Windows NVIDIA hardware
comparison reports**. See [validation.md](validation.md).

The v1 interpreter is deliberately bounded and covers local C driver interfaces
with scalar/output-buffer/opaque-handle arguments. It does not provide arbitrary
structures, callbacks, CPU instruction probing, shell probes, remote updates,
online revocation, a native-code sandbox, or unlimited reference execution for
pathological inputs. CPython object identity, mutated returned objects, source
locations, and tracebacks are not cross-language contracts.

The local signing demonstration is functional, but its key is not production
trust. No deployment, publication, or package-registry upload has been
performed.
