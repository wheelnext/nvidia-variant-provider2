"""JSON-defined variant providers. No vendor-specific execution logic."""

from .definition import load_definition, validate_definition
from .engine import Provider, ProviderError, VariantFeatureConfig

__all__ = [
    "Provider",
    "ProviderError",
    "VariantFeatureConfig",
    "load_definition",
    "validate_definition",
]
__version__ = "0.1.0"
