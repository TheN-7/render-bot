import json
from pathlib import Path
from typing import Any, List

from .replay_extract import extract_replay
from .replay_schema import to_legacy_schema


def parse_replay(path: str) -> List[Any]:
    """Deprecated parser retained for compatibility.

    For .wowsreplay input this now uses the canonical extraction pipeline and
    returns a list with metadata-like blocks to preserve historical callers.
    For .json input this returns the top-level object in a list.
    """
    src = Path(path)
    if src.suffix.lower() == ".wowsreplay":
        canonical = extract_replay(path)
        legacy = to_legacy_schema(canonical)
        return [legacy.get("meta", {}), legacy]

    if src.suffix.lower() == ".json":
        data = json.loads(src.read_text(encoding="utf-8"))
        return [data]

    raise ValueError(f"Unsupported replay file type: {src.suffix}")
