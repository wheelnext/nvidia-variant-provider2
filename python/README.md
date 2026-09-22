# Variant provider (Python)

An independent interpreter for versioned, signed JSON provider definitions.

```python
from variant_provider import Provider

result = Provider().invoke("get_supported_configs")
```

NVIDIA is the bundled definition. External native-call definitions require
trust; JSON Schema validates structure, not the safety of a native library's
behavior. See the workspace `docs/` directory for authoring, signing, and
validation.
