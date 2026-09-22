"""Legacy names only; detection and compatibility policy live in JSON."""

import builtins
import warnings
from functools import cache

from packaging.version import InvalidVersion, Version

from variant_provider import (
    Provider,
    ProviderError,
    VariantFeatureConfig,
    load_definition,
)

_DEFINITION, _ = load_definition()


class NvidiaVariantPlugin:
    namespace = _DEFINITION["provider"]["namespace"]
    is_build_plugin = _DEFINITION["provider"]["is_build_plugin"]

    @classmethod
    @cache
    def _provider(cls):
        return Provider(_DEFINITION)

    @classmethod
    def _invoke(cls, name):
        result = cls._provider().invoke(name)
        for warning in result["warnings"]:
            category = getattr(builtins, warning["category"], UserWarning)
            if not isinstance(category, type) or not issubclass(category, Warning):
                category = UserWarning
            warnings.warn(warning["message"], category, stacklevel=1)
        if "error" in result:
            error = result["error"]
            kind = (
                InvalidVersion
                if error["category"] == "InvalidVersion"
                else getattr(builtins, error["category"], None)
            )
            if isinstance(kind, type) and issubclass(kind, Exception):
                raise kind(error["message"])
            raise ProviderError(**error)
        value = result["value"]
        if name == "umd_version":
            return Version(value["$version"]) if value is not None else None
        if name == "get_sm_architectures":
            return tuple(value) if value is not None else None
        if name in ("get_all_configs", "get_supported_configs"):
            return [VariantFeatureConfig(**item) for item in value]
        return value

    @classmethod
    def clear_cache(cls):
        cls._provider().clear_cache()


# Bind fixed legacy names without generating executable source.
def _method(name):
    def invoke(cls):
        return cls._invoke(name)

    invoke.__name__ = name
    invoke.cache_clear = lambda: NvidiaVariantPlugin._provider().clear_cache(
        NvidiaVariantPlugin._provider().definition["exports"][name]
    )
    return classmethod(invoke)


for _name in (
    "generate_all_umd_values",
    "generate_all_sm_values",
    "umd_version",
    "get_sm_architectures",
    "get_supported_configs",
    "get_all_configs",
):
    setattr(NvidiaVariantPlugin, _name, _method(_name))
