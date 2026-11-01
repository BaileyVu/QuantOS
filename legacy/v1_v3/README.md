# Legacy assets

This directory preserves the pre-paper QuantOS research and V1 implementation.
It is frozen historical material; the supported application is `quantos.paper`.
Legacy Python packages live under `src/` here and are not installed by default.

Run legacy tests from the repository root with:

```sh
PYTHONSAFEPATH=1 PYTHONPATH=legacy/v1_v3:legacy/v1_v3/src python -m unittest discover -s legacy/v1_v3/tests -t legacy/v1_v3
```

Some optional research tests require the `research` dependencies from `pyproject.toml`.
