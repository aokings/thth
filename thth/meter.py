"""手数の計測（設計「監査と審査の機械化と前向きな断り」§4・§5・段 F）。

断りが増えて使い勝手が落ちていないかを数で見る。**読むだけの集計と、計測の控えの 1 行だけ。外に送らない。**

控え（`state/<account>/command_meter.ndjson`・0600）の 1 行は **`{at, account, command, outcome, code, marks}`
の 6 つだけ**:

- `command` は argparse が知っている命令の名前（`plaza post`）か MCP の道具の名前（`mcp thth_plaza_post`）。
  引数の値は入れない。
- `outcome` は `ok`・`refused`・`first_stage`（二段確認の 1 段目・断りに数えない・`refusals.mark_first_stage`）。
- `code` は断ったときの先頭の静的な符丁（`refusals.reason_code` が取り出したもの）。通ったときは null。
- `marks` は決まった旗の名前だけ（`MARKS`）。旗の値は入れない。
- 本文・引数の値・パス・秘密は残さない。

`refusals.record`（直近 5 件・`--from-last-refusal` の再現手順）は今のまま。5 件では率が出ない
（設計 §2 の (4)）ので、通った命令も数える控えをここに別に置く。

**読むだけの命令は通ったときに何も書かない約束**（`read_coordination.READS`・報告の口・`article diff`）を
崩さない: 通った読む命令は控えず（`counted_on_success`）、断られたとき（`last_refusals.json` を既に書く）
だけ控える。だから率の分母は「書く・送る・置く命令と lint」で、読む命令の断りは数だけ別に出す。

大きさと期間: `KEEP_DAYS`（14 日・直近 7 日とその前の 7 日を比べるため）より古い行は書くときに落とす
（先頭の 1 行だけ見て、14 日より古ければ詰める・毎回全体は書き直さない）。時計が戻って行の順が崩れると
先頭だけでは見えないので、最後の行と日が変わる 1 行でも詰める（詰めると時刻の順に並び直る）。
ファイルが `MAX_BYTES` を超えたら、14 日の内の新しい `MAX_ROWS` 行に詰め直す。置き場は口座の state の中
なので、`thth account leave` で state ごと消える。

集計（`rates`・`violations`）は 0 なら None を返す——呼ぶ側は鍵を足さない（0 なら出さない）。
"""
from __future__ import annotations

import datetime
import json
import os
import re

from . import accounts, jst

FILE = "command_meter.ndjson"
KEYS = ("at", "account", "command", "outcome", "code", "marks")
OUTCOMES = ("ok", "refused", "first_stage")
# 残してよい旗（名前だけ）。違反の 3 型を見つけるのに要るものだけ。
MARKS = {"--preview": "preview", "--open": "open", "--production": "production", "--dry-run": "dry_run"}
MARK_NAMES = tuple(MARKS.values())
KEEP_DAYS = 14
MAX_BYTES = 512 * 1024
MAX_ROWS = 4000
WINDOW_DAYS = 7
REPEAT_MINUTES = 10
REPEAT_TIMES = 3
# 違反の 2 型と 3 型で「前に」を探す長さ。
LOOKBACK_HOURS = 24
TOP_CODES = 3
# 命令の形・符丁の形（`refusals` と同じ物差し）。
_COMMAND = re.compile(r"[a-z][a-z0-9_-]*(?: [a-z][a-z0-9_-]*){0,2}")
_CODE = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+")
_EXIT = re.compile(r"exit_[0-9]{1,3}")
MAX_CODE = 64
# 数えない命令（何も書かないと約束した乾式の試験）。
SKIP_FLAGS = ("--rehearse",)
PLAZA_POST = ("plaza post", "mcp thth_plaza_post")
# 報告の口は口座の state に何も足さない約束（`refusals.SKIP_COMMANDS` と同じ・報告は私有の置き場だけ）。
SKIP_COMMANDS = ("report",)
# **読むだけの命令は、通ったときに何も書かない**（`read_coordination.READS`・試験が state を見て縛る）。
# だから通った読む命令は数えない。断られたときは `last_refusals.json` を既に書くので、断りだけ数える。
# lint だけは通っても数える（違反の 3 型「send の前に lint が無い」を見るため・原稿の account に）。
SUCCESS_READS = ("lint",)
# READS の外でも、通ったときに「ファイルは書かない」と約束した命令（試験 test_v400_s5_detect）。
QUIET_COMMANDS = ("article diff",)
# MCP のサーバ型で、通ったときに数える道具（書く・送る・置く口だけ。読む道具は CLI と同じく数えない）。
MCP_COUNTED = frozenset((
    "thth_draft_put", "thth_send_request", "thth_retract_request", "thth_schedule_request",
    "thth_media_upload_url", "thth_media_complete", "thth_bookmark_request",
    "thth_bookmark_batch_request", "thth_bookmark_batch_cancel",
    "thth_plaza_post", "thth_plaza_reply", "thth_plaza_update", "thth_conflict_resolve"))


def counted_on_success(command) -> bool:
    """通った命令を控えに数えるか（数えない命令は断られたときだけ数える）。"""
    if not isinstance(command, str):
        return False
    if command.startswith("mcp "):
        return command[4:] in MCP_COUNTED
    from . import read_coordination
    first = command.split(" ", 1)[0]
    if first in SKIP_COMMANDS or any(command == quiet or command.startswith(quiet + " ")
                                     for quiet in QUIET_COMMANDS):
        return False
    return first in SUCCESS_READS or first not in read_coordination.READS


def marks_of_argv(argv) -> list:
    """argv の中の決まった旗の名前（`--preview` と `--preview=…` のどちらも）。値は見ない。"""
    found = set()
    for token in argv or ():
        if not isinstance(token, str):
            continue
        flag = token.split("=", 1)[0]
        if flag in MARKS:
            found.add(MARKS[flag])
    return sorted(found)


def marks_of_arguments(arguments) -> list:
    """MCP の引数から決まった旗の名前（`preview: true`・`visibility: open`）。値は残さない。"""
    if not isinstance(arguments, dict):
        return []
    found = set()
    if arguments.get("preview") is True:
        found.add("preview")
    if arguments.get("visibility") == "open":
        found.add("open")
    return sorted(found)


def _valid(row, account) -> bool:
    return (isinstance(row, dict) and set(row) == set(KEYS)
            and row["account"] == account
            and isinstance(row["at"], str) and jst.parse(row["at"]) is not None
            and isinstance(row["command"], str) and bool(_COMMAND.fullmatch(row["command"]))
            and row["outcome"] in OUTCOMES
            and (row["code"] is None
                 or (isinstance(row["code"], str) and len(row["code"]) <= MAX_CODE
                     and bool(_CODE.fullmatch(row["code"]) or _EXIT.fullmatch(row["code"]))))
            and (row["code"] is None) == (row["outcome"] != "refused")
            and isinstance(row["marks"], list) and all(mark in MARK_NAMES for mark in row["marks"]))


def _path(account):
    return os.path.join(accounts.state_dir_for(account), FILE)


def read(account) -> list:
    """その口座の控え（古い順・形の正しい行だけ）。他の口座の行は返さない。"""
    if not accounts.name_is_safe(account):
        return []
    rows = []
    try:
        with open(_path(account), encoding="utf-8") as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if _valid(row, account):
                    rows.append(row)
    except OSError:
        return []
    rows.sort(key=lambda row: jst.parse(row["at"]))
    return rows


def _writable(account) -> bool:
    """台帳のある・止めていない・置き場の権限が安全な口座だけに書く（`refusals.record` と同じ物差し）。"""
    if not isinstance(account, str) or not accounts.name_is_safe(account):
        return False
    try:
        if account not in accounts.list_account_names():
            return False
        from . import stop_observation
        if stop_observation.diagnostic(account)["error"]:
            return False
        accounts.load_account(account)
    except (accounts.AccountError, OSError, ValueError, TypeError):
        return False
    return True


def note(account, *, command, outcome, code=None, marks=(), now=None) -> bool:
    """1 行足す。**控えの失敗で命令の結果を変えない**（呼ぶ側も例外を握る）。"""
    if outcome != "refused":
        code = None
        if not counted_on_success(command):
            return False
    elif (isinstance(command, str)
          and (command.split(" ", 1)[0] in SKIP_COMMANDS or command.startswith("mcp thth_report_"))):
        return False
    row = {"at": jst.iso(now or jst.now_jst()), "account": account, "command": command,
           "outcome": outcome, "code": code, "marks": sorted(set(marks or ()))}
    if not _valid(row, account) or not _writable(account):
        return False
    directory = accounts.state_dir_for(account)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    target = _path(account)
    at = jst.parse(row["at"])
    new_day = _day_changes(target, at)
    line = (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)
    os.chmod(target, 0o600)
    if os.path.getsize(target) > MAX_BYTES or new_day or _head_is_stale(target, at):
        _compact(account, at)
    return True


def _day_changes(target, at) -> bool:
    """控えの最後の行と日が違うか（末尾だけ読む。空なら False・読めない末尾は詰めて落とす）。"""
    try:
        with open(target, "rb") as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - 1024))
            rows = stream.read().splitlines()
    except OSError:
        return False
    if not rows:
        return False
    try:
        last = jst.parse(json.loads(rows[-1])["at"])
    except (ValueError, TypeError, KeyError):
        return True
    return last is None or last.date() != at.date()


def _head_is_stale(target, now) -> bool:
    """先頭の行が 14 日より古いか（1 行だけ読む。読めない先頭は詰めて落とす）。"""
    try:
        with open(target, encoding="utf-8") as stream:
            first = stream.readline()
        at = jst.parse(json.loads(first)["at"])
    except (OSError, ValueError, TypeError, KeyError):
        return True
    if at is None:
        return True
    return at < now - datetime.timedelta(days=KEEP_DAYS)


def _compact(account, now) -> None:
    """14 日より古い行を落とし、新しい `MAX_ROWS` 行に詰め直す（入れ替えは原子的）。"""
    import uuid
    floor = now - datetime.timedelta(days=KEEP_DAYS)
    rows = [row for row in read(account) if jst.parse(row["at"]) >= floor][-MAX_ROWS:]
    target = _path(account)
    temp = f"{target}.{uuid.uuid4().hex}.tmp"
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            for row in rows:
                stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        os.replace(temp, target)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def draft_accounts(paths, limit=50) -> list:
    """lint した原稿の front-matter の `account`（lint には口座の引数が無い）。読めなければ数えない。"""
    from . import bundle as bundle_mod, queuefile
    names = set()
    for path in list(paths or ())[:limit]:
        try:
            raw = open(path, encoding="utf-8").read()
            draft = (bundle_mod.parse_text(raw, path) if bundle_mod.is_bundle_text(raw)
                     else queuefile.parse_text(raw, path))
        except Exception:  # noqa: BLE001 — 読めない原稿は lint が断る（ここは数えないだけ）
            continue
        account = (draft.front_matter or {}).get("account")
        if accounts.name_is_safe(account):
            names.add(account)
    return sorted(names)


# ------------------------------------------------------------------ 集計（読むだけ）

def _window(rows, now, days, offset=0):
    until = now - datetime.timedelta(days=offset)
    since = until - datetime.timedelta(days=days)
    return [row for row in rows if since < jst.parse(row["at"]) <= until]


def _per_100(refused, commands):
    return round(refused * 100 / commands, 1) if commands else None


def _repeats(refused_rows, minutes=REPEAT_MINUTES):
    """同じ符丁が前の同じ符丁から `minutes` 分以内に来た回数（2 回目からを数える）。"""
    last, n = {}, 0
    for row in refused_rows:
        at = jst.parse(row["at"])
        before = last.get(row["code"])
        if before is not None and at - before <= datetime.timedelta(minutes=minutes):
            n += 1
        last[row["code"]] = at
    return n


def rate(account, now=None) -> dict | None:
    """口座 1 つの断りの率（直近 7 日・100 命令あたり）。7 日に断りが 0 なら None。"""
    now = now or jst.now_jst()
    rows = read(account)
    current = _window(rows, now, WINDOW_DAYS)
    refused_all = [row for row in current if row["outcome"] == "refused"]
    if not refused_all:
        return None
    # 分母は「通っても数える命令」だけ（読む命令は通ったときに書かないので、断りだけが控えにある）。
    counted = [row for row in current if counted_on_success(row["command"])]
    refused = [row for row in counted if row["outcome"] == "refused"]
    codes: dict = {}
    for row in refused_all:
        codes[row["code"]] = codes.get(row["code"], 0) + 1
    top = sorted(codes.items(), key=lambda item: (-item[1], item[0]))[:TOP_CODES]
    previous = [row for row in _window(rows, now, WINDOW_DAYS, offset=WINDOW_DAYS)
                if counted_on_success(row["command"])]
    previous_refused = sum(1 for row in previous if row["outcome"] == "refused")
    return {"account": account, "window_days": WINDOW_DAYS,
            "commands": len(counted), "refusals": len(refused),
            "per_100": _per_100(len(refused), len(counted)),
            # 読む命令の断り（分母が無いので数だけ）。
            "read_refusals": len(refused_all) - len(refused),
            "repeats_10min": _repeats(refused_all),
            "top_codes": [{"code": code, "n": n} for code, n in top],
            "previous_7d": ({"commands": len(previous), "refusals": previous_refused,
                             "per_100": _per_100(previous_refused, len(previous))} if previous else None),
            "denominator": "書く・送る・置く命令と lint（読むだけの命令は通ったときに何も書かないので数えない）"}


def _same_code_bursts(refused_rows):
    """同じ符丁を 10 分に 3 回（3 回目からを数える）。"""
    seen: dict = {}
    n, codes = 0, set()
    span = datetime.timedelta(minutes=REPEAT_MINUTES)
    for row in refused_rows:
        at = jst.parse(row["at"])
        recent = [t for t in seen.get(row["code"], []) if at - t <= span]
        recent.append(at)
        seen[row["code"]] = recent
        if len(recent) >= REPEAT_TIMES:
            n += 1
            codes.add(row["code"])
    return n, sorted(codes)


def _before(rows, index, test, hours=LOOKBACK_HOURS):
    at = jst.parse(rows[index]["at"])
    floor = at - datetime.timedelta(hours=hours)
    return any(test(row) for row in rows[:index] if jst.parse(row["at"]) >= floor)


def violation(account, now=None) -> dict | None:
    """操作の記録から違反の 3 型（直近 7 日・読むだけ）。全部 0 なら None。

    - `same_code_3_in_10min`: 同じ符丁の断りを 10 分に 3 回（3 回目からの数）。
    - `post_without_preview`: 広場に置いた（通った `plaza post`・`--preview` でない）のに、その前 24 時間に
      同じ口座の `plaza post --preview` が無い（open の一段目だけで確定した形も入る）。
    - `send_without_check`: `send --production` が通ったのに、その前 24 時間に同じ口座の `lint` も
      dry-run の `send`（`--production` なし・`--dry-run`）も無い。
    """
    now = now or jst.now_jst()
    rows = _window(read(account), now, WINDOW_DAYS + 1)
    in_window = [i for i, row in enumerate(rows)
                 if jst.parse(row["at"]) > now - datetime.timedelta(days=WINDOW_DAYS)]
    refused = [rows[i] for i in in_window if rows[i]["outcome"] == "refused"]
    bursts, burst_codes = _same_code_bursts(refused)

    def preview(row):
        return row["command"] in PLAZA_POST and "preview" in row["marks"]

    def checked(row):
        return (row["command"] == "lint"
                or (row["command"] == "send"
                    and ("dry_run" in row["marks"] or "production" not in row["marks"])))

    unpreviewed = sum(1 for i in in_window
                      if rows[i]["command"] in PLAZA_POST and rows[i]["outcome"] == "ok"
                      and "preview" not in rows[i]["marks"] and not _before(rows, i, preview))
    unchecked = sum(1 for i in in_window
                    if rows[i]["command"] == "send" and rows[i]["outcome"] == "ok"
                    and "production" in rows[i]["marks"] and "dry_run" not in rows[i]["marks"]
                    and not _before(rows, i, checked))
    if not (bursts or unpreviewed or unchecked):
        return None
    return {"account": account, "window_days": WINDOW_DAYS,
            "same_code_3_in_10min": bursts, "same_code_3_in_10min_codes": burst_codes,
            "post_without_preview": unpreviewed, "send_without_check": unchecked}


def summary(names, now=None) -> dict | None:
    """口座の列の率と違反。どちらも 0 なら None（呼ぶ側は鍵を足さない）。"""
    now = now or jst.now_jst()
    rates, violations = [], []
    for name in sorted(set(names or ())):
        try:
            one = rate(name, now)
            bad = violation(name, now)
        except Exception:  # noqa: BLE001 — 計測の読みで 1 枚を落とさない
            continue
        if one is not None:
            rates.append(one)
        if bad is not None:
            violations.append(bad)
    if not rates and not violations:
        return None
    return {"window_days": WINDOW_DAYS, "rates": rates, "violations": violations,
            "basis": "道具の控え（state/<account>/command_meter.ndjson・命令の名前と符丁と時刻だけ）"}


# ------------------------------------------------------------------ 人向けの 1 行

def rate_line(meter) -> str | None:
    """`手数（7 日）: 口座 断り 100 命令あたり x（r/n）・同じ符丁の 10 分内の繰り返し k`。"""
    rows = (meter or {}).get("rates") or []
    if not rows:
        return None
    parts = []
    for row in rows:
        text = (f"{row['account']} 断り 100 命令あたり {row['per_100']}（{row['refusals']}/{row['commands']}）"
                if row["commands"] else f"{row['account']}")
        if row.get("read_refusals"):
            text += f"・読む命令の断り {row['read_refusals']}"
        if row["repeats_10min"]:
            text += f"・同じ符丁の 10 分内の繰り返し {row['repeats_10min']}"
        previous = row.get("previous_7d")
        if previous and previous.get("per_100") is not None:
            text += f"・前の 7 日 {previous['per_100']}"
        parts.append(text)
    return f"手数（{WINDOW_DAYS} 日）: " + "／".join(parts)


def violation_line(meter) -> str | None:
    """違反の 3 型を 1 行。直し方（次から打つ形）を添える（前向きな断りの文体）。"""
    rows = (meter or {}).get("violations") or []
    if not rows:
        return None
    parts, fixes = [], []
    for row in rows:
        items = []
        if row["same_code_3_in_10min"]:
            items.append(f"同じ断りを 10 分に 3 回 {row['same_code_3_in_10min']}"
                         f"（{'・'.join(row['same_code_3_in_10min_codes'])}）")
            fixes.append("同じ断りは「通すには」の命令を先に")
        if row["post_without_preview"]:
            items.append(f"--preview なしで広場に置いた {row['post_without_preview']}")
            fixes.append("広場は先に thth plaza post … --preview")
        if row["send_without_check"]:
            items.append(f"lint か --dry-run なしで send {row['send_without_check']}")
            fixes.append("send は先に thth lint か --dry-run")
        parts.append(f"{row['account']} " + "・".join(items))
    seen = []
    for fix in fixes:
        if fix not in seen:
            seen.append(fix)
    return (f"手順の飛ばし（{WINDOW_DAYS} 日）: " + "／".join(parts)
            + "。通る順: " + "・".join(seen))
