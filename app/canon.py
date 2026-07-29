import hashlib
import json


def _canon(obj):
    """Recursively sort dict keys so semantically-identical JSON hashes the same
    regardless of key order."""
    if isinstance(obj, dict):
        return {k: _canon(obj[k]) for k in sorted(obj.keys())}
    if isinstance(obj, list):
        return [_canon(v) for v in obj]
    return obj


def canonical_hash(obj) -> str:
    canon = _canon(obj)
    compact = json.dumps(canon, separators=(",", ":"), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(compact.encode("utf-8")).hexdigest()


def message_content_hash(message: dict) -> str:
    """Hash of the message only (configuration is intentionally excluded)."""
    return canonical_hash(message)


def package_content_hash(package: dict) -> str:
    return canonical_hash(package)
