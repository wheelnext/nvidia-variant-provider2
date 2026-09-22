# NVIDIA variant provider compatibility facade

Preserves `nvidia_variant_provider.plugin:NvidiaVariantPlugin` and the
`variant_plugins` entry point while delegating execution to `variant-provider`.
All detection and compatibility policy comes from the bundled JSON definition.
