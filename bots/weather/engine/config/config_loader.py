"""
Optional YAML config loader. Primary config remains config.settings (Pydantic + env).
Use this for file-based tuning (e.g. monitoring thresholds, collection params) without code changes.
Env vars with prefix PIPELINE_ override YAML (e.g. PIPELINE_DATABASE_BATCH_SIZE -> database.batch_size).
"""
import os
from pathlib import Path
from typing import Any, Dict, Optional

from structlog import get_logger

logger = get_logger()

try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


def _default_config() -> Dict[str, Any]:
    return {
        "app": {"name": "polymarket-ai-v2", "environment": "development"},
        "rate_limits": {"polymarket_api": 90, "batch_delay": 0.7},
        "database": {"batch_size": 1000, "max_retries": 3},
        "collection": {"daily": {}, "validation": {}},
        "monitoring": {"thresholds": {}},
        "backup": {"retention_days": 7},
        "logging": {"level": "INFO"},
    }


def _set_nested(d: Dict[str, Any], path: list, value: Any) -> None:
    for key in path[:-1]:
        d = d.setdefault(key, {})
    try:
        if path[-1].isdigit():
            d[int(path[-1])] = value
        else:
            d[path[-1]] = value
    except (ValueError, TypeError):
        d[path[-1]] = value


def _apply_env_overrides(config: Dict[str, Any], prefix: str = "PIPELINE_") -> None:
    for key, value in os.environ.items():
        if not key.startswith(prefix):
            continue
        path_str = key[len(prefix):].lower()
        path = path_str.split("_")
        if len(path) < 2:
            continue
        # Coerce numeric strings
        if value.lower() in ("true", "1", "yes"):
            value = True
        elif value.lower() in ("false", "0", "no"):
            value = False
        elif value.isdigit():
            value = int(value)
        elif value.replace(".", "", 1).isdigit():
            value = float(value)
        _set_nested(config, path, value)


class Config:
    """Optional file-based config (YAML). Singleton; load_config() once at startup if needed."""

    _instance: Optional["Config"] = None
    _config: Dict[str, Any]

    def __new__(cls) -> "Config":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if not hasattr(self, "_config") or self._config is None:
            self._config = _default_config()

    def load_config(self, config_path: Optional[str] = None) -> None:
        path = Path(config_path or "config/config.yaml")
        if not path.is_absolute():
            path = Path.cwd() / path
        if not _HAS_YAML:
            self._config = _default_config()
            return
        if not path.exists():
            self._config = _default_config()
            _apply_env_overrides(self._config)
            return
        try:
            with open(path, encoding="utf-8") as f:
                loaded = yaml.safe_load(f) or {}
            defaults = _default_config()

            def merge(base: Dict, override: Dict) -> None:
                for k, v in override.items():
                    if k in base and isinstance(base[k], dict) and isinstance(v, dict):
                        merge(base[k], v)
                    else:
                        base[k] = v
            merge(defaults, loaded)
            self._config = defaults
            _apply_env_overrides(self._config)
        except Exception as e:
            logger.warning("Config file load failed, using defaults: %s", e)
            self._config = _default_config()
            _apply_env_overrides(self._config)

    def get(self, path: str, default: Any = None) -> Any:
        """Get value by dot path, e.g. config.get('database.batch_size')."""
        keys = path.split(".")
        value: Any = self._config
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        return value

    def get_section(self, section: str) -> Dict[str, Any]:
        """Get entire section as dict."""
        return self._config.get(section, {})

    def set(self, path: str, value: Any) -> None:
        keys = path.split(".")
        _set_nested(self._config, keys, value)


# Singleton access; call load_config() before first get() if using YAML file.
config = Config()
