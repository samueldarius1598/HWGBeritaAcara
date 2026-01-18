import os
from pathlib import Path

import tomllib

_SECRETS_CACHE = None


def _load_secrets():
    global _SECRETS_CACHE
    if _SECRETS_CACHE is not None:
        return _SECRETS_CACHE
    secrets_paths = []
    env_path = os.environ.get("SECRETS_PATH")
    if env_path:
        secrets_paths.append(Path(env_path))
    secrets_paths.append(Path("secrets.toml"))
    secrets_paths.append(Path(".streamlit") / "secrets.toml")
    for secrets_path in secrets_paths:
        if not secrets_path.exists():
            continue
        try:
            _SECRETS_CACHE = tomllib.loads(secrets_path.read_text(encoding="utf-8"))
            return _SECRETS_CACHE
        except Exception:
            _SECRETS_CACHE = {}
            return _SECRETS_CACHE
    _SECRETS_CACHE = {}
    return _SECRETS_CACHE


def get_setting(key, default=None):
    value = os.environ.get(key)
    if value not in (None, ""):
        return value
    return _load_secrets().get(key, default)


def get_report_api_key():
    return get_setting("REPORT_API_KEY") or ""
