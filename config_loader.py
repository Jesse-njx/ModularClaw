import json
import os


class Config:
    _configs = {}
    _config_dir = os.path.join(os.path.dirname(__file__), "config")
    _indexed_names = None

    @classmethod
    def _index_config_names(cls):
        if cls._indexed_names is not None:
            return

        exact_names = {}
        lower_seen = {}
        for entry in os.listdir(cls._config_dir):
            if not entry.endswith(".json"):
                continue
            stem = os.path.splitext(entry)[0]
            key = stem.lower()
            if key in lower_seen and lower_seen[key] != stem:
                raise NameConflictError(
                    f"Config name conflict: '{lower_seen[key]}.json' and '{stem}.json' differ only by case."
                )
            lower_seen[key] = stem
            exact_names[stem] = os.path.join(cls._config_dir, entry)
        cls._indexed_names = exact_names

    @classmethod
    def load(cls, name: str) -> dict:
        if name in cls._configs:
            return cls._configs[name]

        cls._index_config_names()

        if not isinstance(name, str) or not name:
            return {}

        # Strict naming rule: config file stem must match the requested name exactly.
        config_path = cls._indexed_names.get(name)
        if not config_path:
            return {}

        with open(config_path, "r", encoding="utf-8") as f:
            cls._configs[name] = json.load(f)
            return cls._configs[name]

    @classmethod
    def get(cls, name: str, key: str = None, default=None):
        config = cls.load(name)
        if key is None:
            return config
        return config.get(key, default)


class VersionMismatchError(Exception):
    pass


class NameConflictError(Exception):
    pass
