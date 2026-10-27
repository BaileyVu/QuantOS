# Legacy assets

This directory preserves the pre-paper QuantOS research and V1 implementation.
It is frozen historical material; the supported application is `quantos.paper`.
Legacy Python packages live under `src/` here and are not installed by default.

Run legacy tests from the repository root with:

```sh
PYTHONPATH=legacy_assets:legacy_assets/src python -m unittest discover -s legacy_assets/tests -t legacy_assets
```

Some optional research tests require the `research` dependencies from `pyproject.toml`.
