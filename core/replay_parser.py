import json
import zlib
from typing import Any, List


def _try_decompress(blob: bytes) -> bytes | None:
    """
    Best-effort zlib decompression helper.
    Returns None if the input is not valid zlib data.
    """
    try:
        return zlib.decompress(blob)
    except Exception:
        return None


def parse_replay(path: str) -> List[Any]:
    """
    Very forgiving WoWS replay reader.

    It scans the binary replay file, tries to split it into chunks,
    and for each chunk tries:
    - direct UTF‑8 JSON decode
    - zlib‑decompress + UTF‑8 JSON decode

    All successfully parsed JSON objects are returned in a list,
    in file order.
    """
    with open(path, "rb") as f:
        data = f.read()

    json_blocks: List[Any] = []

    # Split on NULL bytes, which commonly separate segments in BigWorld files
    for raw_chunk in data.split(b"\x00"):
        chunk = raw_chunk.strip()
        if not chunk:
            continue

        candidates = [chunk]

        decompressed = _try_decompress(chunk)
        if decompressed:
            candidates.append(decompressed)

        parsed_this_chunk = False
        for candidate in candidates:
            try:
                text = candidate.decode("utf-8")
            except UnicodeDecodeError:
                continue

            text = text.strip()
            if not text or not (text.startswith("{") or text.startswith("[")):
                continue

            try:
                obj = json.loads(text)
            except json.JSONDecodeError:
                continue

            json_blocks.append(obj)
            parsed_this_chunk = True
            break

        # Skip silently if nothing could be parsed from this chunk
        if not parsed_this_chunk:
            continue

    return json_blocks

