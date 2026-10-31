"""API key loading. Values never appear in repr/str; file must be private and outside the repo."""
from __future__ import annotations

import json
import os
from pathlib import Path

ENV = "QUANTOS_KEY_FILE"


class KeyError_(RuntimeError):
    """Key file refused (fail-closed)."""


class Keys:
    __slots__ = ("_k", "_s")

    def __init__(self, api_key: str, api_secret: str):
        if not (isinstance(api_key, str) and isinstance(api_secret, str) and api_key and api_secret):
            raise KeyError_("key file needs non-empty api_key and api_secret")
        self._k, self._s = api_key, api_secret

    @property
    def api_key(self) -> str: return self._k
    @property
    def api_secret(self) -> str: return self._s
    def __repr__(self) -> str: return "Keys(****)"
    __str__ = __repr__
    def __reduce__(self): raise TypeError("Keys cannot be pickled")


def _inside_git_repo(path: Path) -> bool:
    return any((p / ".git").exists() for p in (path.parent, *path.parent.parents))


def load_keys(path: str | os.PathLike | None = None) -> Keys:
    raw = path if path is not None else os.environ.get(ENV)
    if not raw:
        raise KeyError_(f"no key file configured (set {ENV})")
    p = Path(raw).expanduser().resolve()
    if _inside_git_repo(p):
        raise KeyError_("key file must not be inside a git repository")
    try:
        st = p.stat()
    except OSError:
        raise KeyError_("key file not readable") from None
    if not p.is_file() or st.st_mode & 0o077:
        raise KeyError_("key file must be a regular file with mode 600 (no group/world access)")
    try:
        data = json.loads(p.read_text())
        return Keys(data["api_key"], data["api_secret"])
    except (OSError, ValueError, KeyError, TypeError):
        raise KeyError_("key file malformed; expected JSON {api_key, api_secret}") from None
