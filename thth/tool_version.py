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
    for path in (NOTES_ROOT / 'docs').glob('リリースノート_*.md'):
        match = re.fullmatch(r'リリースノート_(\d+\.\d+\.\d+)_\d{4}-\d{2}-\d{2}\.md', path.name)
        if match and path.is_file():
            version = numbers(match[1])
            if version <= current and (previous is None or previous < version):
                found.append((version, path.name))
    found.sort()
    notes = found if previous is not None else found[-1:]
    return dict(version=__version__, previous_version=previous_version if previous else None,
                changed_since_last_read=(previous != current) if previous else None,
                release_notes=['docs/' + name for _, name in notes],
                notes_root=str(NOTES_ROOT), notes_root_local_hint='~/Developer/thth')
