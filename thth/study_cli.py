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


def _day(text, label):
    """YYYY-MM-DD（JST の 0 時）。"""
    import datetime
    try:
        day = datetime.date.fromisoformat(text)
    except (TypeError, ValueError):
        raise study_report.StudyError(f'{label} は YYYY-MM-DD の日付です（例 2026-11-30）') from None
    return datetime.datetime(day.year, day.month, day.day, tzinfo=jst.JST)


def declare(path, *, account, hypothesis, change, since, until=None, baseline_days=7,
            study_id=None, by=None, now=None):
    """期間で宣言する形（週の振り返りの A・2026-11-25）。新しい施策JSONを 1 つ書く。

    「`since` の日からこう変えた（変える）」を、投稿の id の列でなく期間で書く。比べる前の群は
    `since` の前の `baseline_days` 日。既にあるファイルは上書きしない（1 つの変更に 1 つの JSON）。
    """
    import datetime
    admin_log.actor(by)  # explicit actor before any file access or creation
    now = now or jst.now_jst()
    if type(baseline_days) is not int or not 1 <= baseline_days <= 365:
        raise study_report.StudyError('--baseline-days は 1〜365 の整数です（既定 7・前の週と比べるなら 7）')
    start = _day(since, '--since')
    end = None if until is None else _day(until, '--until') + datetime.timedelta(days=1)
    if end is not None and end <= start:
        raise study_report.StudyError('--until は --since と同じ日かそれより後の日です（その日を含みます）')
    target = Path(path)
    if target.suffix != '.json':
        raise study_report.StudyError('施策JSONのファイル名は .json で終えてください（例 study/2026-11-30_朝の型.json）')
    if os.path.lexists(target):
        raise study_report.StudyError('そのファイルは既にあります。変更 1 つに施策JSONを 1 つ、別の名前で作ってください')
    if not target.parent.is_dir():
        raise study_report.StudyError('施策JSONを置くフォルダがありません。先にフォルダを作ってから打ち直してください')
    value = {"schema_version": 1, "id": study_id or target.stem, "account": account,
             "hypothesis": hypothesis, "change": change,
             "decision": {"status": "adopted", "by": by, "at": jst.iso(jst.to_jst(now).replace(microsecond=0))},
             "baseline_post_ids": [], "changed_post_ids": [],
             "period": {"basis": "posted_at", "start": jst.iso(start),
                        "end": None if end is None else jst.iso(end),
                        "baseline_start": jst.iso(start - datetime.timedelta(days=baseline_days))}}
    study_report.validate_declaration(value, now)
    data = (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data);stream.flush();os.fsync(stream.fileno())
    return dict(file=str(target), id=value["id"], account=account, period=value["period"],
                added=True, by=by)


def cmd(args):
    period_flags = [flag for flag, value in (('--since', args.since), ('--until', args.until),
                                             ('--hypothesis', args.hypothesis), ('--change', args.change),
                                             ('--account', args.account), ('--id', args.id))
                    if value is not None]
    try:
        if args.since is not None:
            if args.target is not None or args.baseline:
                raise study_report.StudyError('期間の形（--since）と投稿の id（<post_id>・--baseline）は一緒に使えません。どちらか一方で打ち直してください')
            missing = [flag for flag, value in (('--account', args.account), ('--hypothesis', args.hypothesis),
                                                ('--change', args.change)) if not value]
            if missing:
                raise study_report.StudyError('期間の形には ' + '・'.join(missing) + ' も要ります（例 thth study add <施策JSON> --since 2026-11-30 --account <口座> --hypothesis <仮説> --change <変えること> --by <名前>）')
            result = declare(args.file, account=args.account, hypothesis=args.hypothesis,
                             change=args.change, since=args.since, until=args.until,
                             baseline_days=args.baseline_days, study_id=args.id, by=args.by)
        else:
            if period_flags:
                raise study_report.StudyError(f'{period_flags[0]} は期間の形の旗です。--since <YYYY-MM-DD> を付けて打ち直してください')
            if args.target is None:
                raise study_report.StudyError('投稿の id か queue のパス、または --since <YYYY-MM-DD>（期間の形）が要ります')
            result = add(args.file, args.target, baseline=args.baseline, by=args.by)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr);return 2
    if args.json:print(json.dumps(result, ensure_ascii=False))
    elif 'period' in result:
        period = result['period']
        print(f"宣言しました: {result['file']}（{result['account']}・{period['start'][:10]} から"
              f"{'・' + period['end'][:10] + ' の前まで' if period['end'] else ''}・比べる前は {period['baseline_start'][:10]} から・{result['by']}）")
        print(f"結果は thth study-report {result['file']} で読めます（変えた期間の投稿が 24 時間を越えてから）")
    else:print(f"{result['group']}: {result['post_id']}（{'追加' if result['added'] else '登録済み'}・{result['by']}）")
    return 0


def register(sub):
    parser = sub.add_parser('study', help='利用者の施策JSONを編集する（commitしない）')
    actions = parser.add_subparsers(dest='study_command', required=True)
    addition = actions.add_parser('add')
    addition.add_argument('file')
    addition.add_argument('target', nargs='?', help='投稿の id か queue のパス（期間の形では書かない）')
    addition.add_argument('--baseline', action='store_true')
    # 期間で宣言する形（週の振り返り・2026-11-25）。投稿の id の列の形はそのまま残す。
    addition.add_argument('--since', help='この日（YYYY-MM-DD・JST）から変えた／変える（期間の形）')
    addition.add_argument('--until', help='この日まで（含む・省けば終わりを決めない）')
    addition.add_argument('--baseline-days', dest='baseline_days', type=int, default=7,
                          help='比べる前の群の日数（--since の前・既定 7）')
    addition.add_argument('--account', help='口座（期間の形）')
    addition.add_argument('--hypothesis', help='仮説（期間の形）')
    addition.add_argument('--change', help='変えること 1 つ（期間の形）')
    addition.add_argument('--id', help='施策の id（既定はファイル名）')
    addition.add_argument('--by')
    addition.add_argument('--json', action='store_true')
    addition.set_defaults(func=cmd)
