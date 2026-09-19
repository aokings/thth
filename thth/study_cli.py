"""Explicit local study editing; no adoption, publication, or git action."""
import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from . import admin_log, jst, queuefile, study_report


def add(path, target, *, baseline=False, by=None):
    admin_log.actor(by)  # explicit actor before any file access or creation
    source = Path(path)
    try:
        mode = source.lstat().st_mode
    except OSError:
        raise study_report.StudyError('施策JSONファイルを読めません') from None
    if not stat.S_ISREG(mode):
        raise study_report.StudyError('施策JSONは symlink でない通常ファイルで指定してください')
    now = jst.now_jst()
    value = study_report.load_declaration(source, now)
    candidate = Path(target)
    if candidate.is_file():
        qf = queuefile.parse(str(candidate))
        if qf.malformed or qf.front_matter.get('account') != value['account']:
            raise study_report.StudyError('queue の形式と施策の account の一致を確認してください')
        pid = qf.front_matter.get('post_id')
        if not pid:
            raise study_report.StudyError('queue に post_id がありません')
    elif target.endswith('.md') or target.startswith(('/', './', '../')):
        raise study_report.StudyError('queue のパスを読めません')
    else:
        pid = target
    study_report._string(pid, 2048)
    if any(ord(c) < 32 or ord(c) == 127 for c in pid):
        raise study_report.StudyError('投稿IDに制御文字は使えません')
    key = 'baseline_post_ids' if baseline else 'changed_post_ids'
    changed = pid not in value[key]
    if changed:value[key].append(pid)
    study_report.validate_declaration(value, now)
    if changed:
        temporary = None
        try:
            fd, temporary = tempfile.mkstemp(prefix='.'+source.name+'.', dir=source.parent)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                os.fchmod(stream.fileno(), stat.S_IMODE(mode))
                json.dump(value, stream, ensure_ascii=False, indent=2)
                stream.write('\n');stream.flush();os.fsync(stream.fileno())
            os.replace(temporary, source)
        finally:
            if temporary and os.path.exists(temporary):os.unlink(temporary)
    return dict(file=str(source), group=key, post_id=pid, added=changed, by=by)


def cmd(args):
    try:
        result = add(args.file, args.target, baseline=args.baseline, by=args.by)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr);return 2
    if args.json:print(json.dumps(result, ensure_ascii=False))
    else:print(f"{result['group']}: {result['post_id']}（{'追加' if result['added'] else '登録済み'}・{result['by']}）")
    return 0


def register(sub):
    parser = sub.add_parser('study', help='利用者の施策JSONを編集する（commitしない）')
    actions = parser.add_subparsers(dest='study_command', required=True)
    addition = actions.add_parser('add')
    addition.add_argument('file')
    addition.add_argument('target')
    addition.add_argument('--baseline', action='store_true')
    addition.add_argument('--by')
    addition.add_argument('--json', action='store_true')
    addition.set_defaults(func=cmd)
