"""Installed tool version and local release-note references; no network or writes."""
from pathlib import Path
import re
from . import __version__

NOTES_ROOT = Path(__file__).resolve().parent.parent


def numbers(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d+\.\d+\.\d+', value):
        return None
    return tuple(map(int, value.split('.')))


def summary(previous_version=None):
    current = numbers(__version__)
    previous = numbers(previous_version)
    found = []
    notes_reason = None
    try:
        candidates = list((NOTES_ROOT / 'docs').iterdir())
    except OSError:
        candidates = []
        notes_reason = 'notes_directory_unavailable'
    for path in candidates:
        match = re.fullmatch(r'リリースノート_(\d+\.\d+\.\d+)_\d{4}-\d{2}-\d{2}\.md', path.name)
        if match and path.is_file():
            version = numbers(match[1])
            if version <= current and (previous is None or previous < version):
                found.append((version, path.name))
    found.sort()
    notes = found if previous is not None else found[-1:]
    # **媒体ごとの「出せるもの」表**（設計 2.13.0 §0.1・第 10 段）。静的な 1 本
    # （`thth/media_capabilities.py`）から出る——provider を叩いた結果ではない。
    # LLM が「この媒体に動画は出せるか」を推測せずに読めるよう、`tool` に常に載せる。
    from . import media_capabilities
    return dict(version=__version__, capabilities=media_capabilities.summary(),
                previous_version=previous_version if previous else None,
                changed_since_last_read=(previous != current) if previous else None,
                release_notes=['docs/' + name for _, name in notes],
                notes_root_local_hint='~/Developer/thth', notes_reason=notes_reason)
