"""Read collect attempts, never confuse a skipped lock with completed collection."""
import json
from pathlib import Path
from . import accounts, analytics_comparison as c, jst


def summarize(name, now):
    attempts, incomplete = [], False
    root = Path(accounts.state_dir_for(name))
    try:
        paths = sorted(root.glob('runs-*.ndjson'))
        for path in paths:
            if path.is_symlink() or not path.is_file():
                incomplete = True
                continue
            try:
                lines = path.read_text(encoding='utf-8').splitlines()
            except (OSError, UnicodeError):
                incomplete = True
                continue
            for line in lines:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    incomplete = True
                    continue
                if not isinstance(row, dict):
                    incomplete = True
                    continue
                if row.get('account') != name or row.get('action') != 'collect':
                    continue
                run_id = row.get('run_id')
                at = c._timestamp(run_id[8:]) if isinstance(run_id, str) and run_id.startswith('collect-') else None
                if at is None or at > now:
                    incomplete = True
                    continue
                collected = row.get('collected')
                ok = (True if row.get('status') == 'ok' and type(collected) is int and collected >= 0
                      else False if row.get('status') == 'error' else None)
                attempts.append((at, ok))
    except OSError:
        incomplete = True
    if not attempts:
        return None, ['collection_records_unavailable'] + (['collection_records_incomplete'] if incomplete else [])
    attempts.sort(key=lambda x: (x[0], repr(x[1])))
    last = attempts[-1][0]
    verdicts = {ok for at, ok in attempts if at == last}
    ok = verdicts.pop() if len(verdicts) == 1 else None
    success = [at for at, valid in attempts if valid is True]
    return {'last_success_at': jst.iso(max(success)) if success else None,
            'last_attempt_at': jst.iso(last), 'last_attempt_ok': ok, 'source': 'runs',
            'coverage': 'incomplete' if incomplete else 'local_records_only',
            'last_attempt_reason': 'collection_outcome_unknown' if ok is None else None}, (
                ['collection_records_incomplete'] if incomplete else [])
