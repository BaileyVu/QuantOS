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
        text = p.read_text()
    except OSError:
        raise KeyError_("key file not readable") from None
    if text.lstrip().startswith("{"):
        try:
            data = json.loads(text)
            return Keys(data["api_key"], data["api_secret"])
        except (ValueError, KeyError, TypeError):
            raise KeyError_("key file malformed; expected JSON {api_key, api_secret}") from None
    return _from_env(text)


def _from_env(text: str) -> Keys:
    """KEY=VALUE lines; picks the one *SECRET* name and the one other *KEY* name. Errors name variables only."""
    pairs = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.removeprefix("export ").split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        pairs[name.strip()] = value
    secrets = [n for n in pairs if "SECRET" in n.upper()]
    keys = [n for n in pairs if "KEY" in n.upper() and n not in secrets]
    if len(secrets) != 1 or len(keys) != 1:
        raise KeyError_(f"env key file needs exactly one *SECRET* and one *KEY* variable; found names {sorted(pairs)}")
    return Keys(pairs[keys[0]], pairs[secrets[0]])
