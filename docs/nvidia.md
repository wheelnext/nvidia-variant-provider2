# NVIDIA reference mapping

The frozen oracle is commit `ffea5d3bf372d7a3b4504b4ee295282417af767c` with
vendored packaging commit `3b77a26f5a27473ad3b08194d773f325d018a2d0`. Its
tracked files are preserved. Initialize the pinned dependency with:

```sh
git -C reference-design submodule update --init --recursive
```

| Reference behavior          | Definition                                                      |
| --------------------------- | --------------------------------------------------------------- |
| NVML loading                | `libraries.management`, Windows candidates then default soname  |
| Initialization and shutdown | `functions.init`, `functions.shutdown`, `queries.environment`   |
| CUDA v2/v1 fallback         | Nested `try` in `environment`                                   |
| Driver decoding             | Divide by 1000; remainder divided by 10                         |
| Device enumeration          | Count-v2, handle-v2, per-device capability queries              |
| Highest architecture        | `sm_architectures`: descending sort then index zero             |
| Version catalog             | `all_umd`: majors 15..11, minors 20..0, then major-only         |
| SM catalog                  | `all_sm`: all real values before all virtual values             |
| CUDA bounds                 | `lower`, `upper`; explicit range construction                   |
| SM compatibility            | `supported_sm`: descending real minors, then major-zero virtual |
| Output ordering             | `features` array                                                |
| Public API                  | Six entries in `exports`                                        |

The two environment variables retain their original names:
`NV_VARIANT_PROVIDER_FORCE_CUDA_DRIVER_VERSION` and
`NV_VARIANT_PROVIDER_FORCE_SM_ARCH`. A nonempty override bypasses hardware
selection for that value. The CUDA override also bypasses the detected-version
range check. Empty overrides behave as absent overrides.

The migration deliberately retains these behaviors:

- The driver-supported range is 11.0 through 15.20, inclusive, for detected
  values.
- Override-generated compatible values may fall outside the static catalog.
- CUDA upper bounds exclude the local minor version and retain the reference's
  major-only entries and ordering.
- A system with no readable architecture raises `IndexError` when that method
  indexes its empty architecture list.
- Compute-capability errors skip a device; count/handle/system-query errors
  abort detection. Shutdown is attempted even after initialization failure.
- Only the highest GPU architecture determines supported SM values.
- Old SM values warn and omit the SM feature. Malformed overrides preserve
  reference errors. Driver absence returns no supported configurations.
- Cached methods retain results even if overrides change later in the process.
- Static catalogs never initialize NVML.

The Python facade preserves the existing import, entry point, dataclass fields,
`Version` return, architecture tuple, and warning/error categories.
Cross-language wire comparison preserves values and order, not Python object
identity, traceback locations, or warning source lines.
