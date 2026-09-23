"""施策の広場（設計 3.4.0）——同じ持ち主の媒体どうしで、試した施策と結果を
数字つきで見せ合い、言葉を交わす場。

動機（設計 §0 の前文）: 同じ持ち主が複数の媒体を持っていても、Threads で効いた
ことを Mastodon のセッションが知る道が無かった。施策の宣言（study-report）・
仮説の棚・報告の口はあるが「場」が無い。**場は道具が持ち、数字は道具が付ける。**

規律:

  (a) **置き場は VM の私有**（`$THTH_ROOT/state/_plaza/<plaza_id>.json`・0600・
      ディレクトリは 0700）。骨は報告の口と共通（`thth/private_store.py`）。
      **SNS の台帳には書かない。**
  (b) **見える範囲は既定で project**（同じ持ち主の全 account）。open（参加した
      他の持ち主にも見える）は 1 件ごとの明示の選択で、後から project に戻せる。
      存在しない id と読めない id は同じ `plaza_not_found`（在ることを漏らさない）。
  (c) **open に出すとき、他人の情報を道具が落とす**（`thth/plaza_redact.py`）。
      他の持ち主が読むのは落としたあとの写しだけ。自分の投稿は先頭 60 字と
      permalink まで。
  (d) **参加は持ち主（project）ごと**（`thth admin plaza join`・既定は不参加）。
      不参加の持ち主は open を読めず、出せない。open の 1 件は、置いた持ち主と
      読む持ち主の両方が参加しているときだけ見える。
  (e) **観測は道具が付ける**（study-report と同じ計算・`thth/plaza_observe.py`）。
      人や LLM が本文に書いた数字は「本文」として分けて見せる（観測と呼ばない）。
  (f) 秘密が混ざっていたら置かない（`redact.looks_like_secret()`）。断りは静的な
      理由コード（`REASONS`）と次の一手だけ。変更ログは presence-only。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import secrets

from . import accounts, admin_log, jst, private_store, redact
from . import goals as _goals
from . import __version__

SCHEMA_VERSION = 1
KINDS = ("measure", "finding", "question")
KIND_LABELS = {"measure": "施策", "finding": "気づき", "question": "問い"}
SCOPES = ("project", "open")
REPLY_KINDS = ("comment", "tried", "agree", "disagree", "trial")
REPLY_LABELS = {"comment": "意見", "tried": "うちでも試した", "agree": "賛成", "disagree": "反対",
                "trial": "追試"}
# 本文が要る返信（`disagree` は理由必須・設計 §2）。`tried` は自分の measure への
# リンクが必須で、本文は任意。`agree` は本文を任意にする（賛成の 1 票だけでもよい）。
REPLY_TEXT_REQUIRED = ("comment", "disagree")
# finding の細目（利用者側の設計材料 §9-1）: 型・規則・罠・道具のコツ。
KIND_DETAILS = ("pattern", "rule", "pitfall", "tool_tip")
KIND_DETAIL_LABELS = {"pattern": "型", "rule": "規則", "pitfall": "罠", "tool_tip": "道具のコツ"}
# 見立てと事実の印（§9-4）。**observed は道具だけが付ける**——人や LLM が名乗れるのは
# stated（本文だけ）と hypothesis（見立て）まで。
EVIDENCE_LEVELS = ("observed", "stated", "hypothesis")
DECLARABLE_LEVELS = ("stated", "hypothesis")
EVIDENCE_LABELS = {"observed": "観測あり（道具が付けた）", "stated": "本文だけ", "hypothesis": "見立て"}
# 追試（§9-2）の結果。**再現しなかった報告を同じ重さで見せる**（一覧と比較の表で並べて数える）。
TRIAL_RESULTS = ("reproduced", "not_reproduced", "not_tried")
TRIAL_LABELS = {"reproduced": "再現した", "not_reproduced": "再現しなかった", "not_tried": "試していない"}
VERDICTS = ("adopted", "dropped", "inconclusive")
VERDICT_LABELS = {"adopted": "採用", "dropped": "取りやめ", "inconclusive": "判断保留"}
VIAS = ("cli", "mcp")

TITLE_MAX = 120
BODY_MAX = 8000
REPLY_MAX = 4000
TEXT_MAX = 4000       # 仮説・変えたこと
SCOPE_NOTE_MAX = 120  # 媒体・企画の範囲（自由文・§9-5）
HOW_MAX = 300         # 数字を出し直せる thth の命令（§9-3）
REASON_MAX = 1000     # 判定の理由・非表示の理由
DECLARATIONS_MAX = 8  # 1 件の施策に並べる媒体（宣言）の上限
# 1 account が 1 日に置ける件数（置く＋返信・直近 24 時間で数える・設計 §6）。
DAILY_LIMIT = 30
WINDOW = datetime.timedelta(hours=24)
# 観測の履歴（置いた時点と更新時点）。これより古いものは最初の 1 件を残して詰める。
OBSERVATION_HISTORY_MAX = 20
MAX_FILE_BYTES = 2 * 1024 * 1024
# 一覧・次の一手に出す題の長さ（observe の先頭 60 字と同じ）。
PREVIEW_CHARS = 60
# 退出した持ち主の名義（設計 §6・open の書き込みを残すと選んだとき）。
LEFT_LABEL = "退出した持ち主"

PLAZA_ID = re.compile(r"p\d{8}-[0-9a-f]{8}\Z")
DIRECTORY = "_plaza"
MEMBERS_FILE = "members.json"

REASONS = frozenset((
    "by_required", "invalid_account", "account_unavailable", "invalid_kind",
    "invalid_post", "post_too_long", "secret_detected", "plaza_rate_limited",
    "duplicate_post", "plaza_not_found", "plaza_store_unavailable", "plaza_log_unavailable",
    "plaza_not_joined", "invalid_reply_kind", "reason_required", "measure_link_required",
    "measure_link_invalid", "not_a_measure", "not_owner", "invalid_verdict",
    "invalid_declaration", "declaration_out_of_scope", "plaza_project_required",
    "invalid_visibility", "redaction_unavailable", "invalid_until", "nothing_to_update",
    "plaza_hidden", "already_hidden", "invalid_min_n",
    # 利用者側の設計材料（設計 §9）。
    "invalid_kind_detail", "scope_required", "how_required", "invalid_how",
    "observed_is_tool_only", "invalid_evidence_level", "invalid_trial_result",
    "third_party_handle",
    # 施策の目的（設計 3.6.0 §A2）。4 語の固定。
    "invalid_goal",
    # open にする二段確認（masaru 裁定 09-23）。
    "open_digest_mismatch", "open_requires_cli",
))

NEXT = {
    "by_required": "--by <名前> を付けてください（誰が置いたかを残します）",
    "invalid_account": "account 名を確かめてください（thth account <account>）",
    "account_unavailable": "account の台帳が読めないか停止中です（thth account <account>）",
    "invalid_kind": "--kind は measure（施策）・finding（気づき）・question（問い）のどれかです",
    "invalid_post": "title と本文は空にできません。title は 1 行です",
    "post_too_long": f"title は {TITLE_MAX} 字・本文は {BODY_MAX} 字・返信は {REPLY_MAX} 字・"
                     f"仮説と変えたことは {TEXT_MAX} 字・理由は {REASON_MAX} 字までです",
    "secret_detected": "秘密らしき値（token・Bearer・sk-・32 桁以上の 16 進 等）が含まれます。"
                       "伏せてから置き直してください",
    "plaza_rate_limited": f"1 account につき直近 24 時間で {DAILY_LIMIT} 件（置く＋返信）までです",
    "duplicate_post": "同じ title と本文が 24 時間以内にあります。既存の id に返信してください",
    "plaza_not_found": "plaza_id を確かめてください（thth plaza list <account|project>）",
    "plaza_store_unavailable": "広場の置き場が読めません。管理者に知らせてください",
    "plaza_log_unavailable": "変更ログに書けなかったので置いていません。管理者に知らせてください",
    "plaza_not_joined": "この持ち主は広場（open）に参加していません。open は参加した持ち主どうしの場です"
                        "（参加は管理者が thth admin plaza join <project> で入れます）。"
                        "project の範囲なら今のまま置けます",
    "invalid_reply_kind": "--kind は comment・tried・agree・disagree のどれかです",
    "reason_required": "理由が要ります（disagree は --text-file、判定と非表示は --reason）",
    "measure_link_required": "tried（うちでも試した）には自分の施策の id を --measure で付けてください",
    "measure_link_invalid": "--measure は自分の持ち主が置いた施策（measure）の id です",
    "not_a_measure": "結果の更新と判定は施策（measure）にだけ付けられます",
    "not_owner": "結果の更新・判定・範囲の変更は、置いた持ち主（同じ project）だけができます",
    "invalid_verdict": "--verdict は adopted・dropped・inconclusive のどれかです",
    "invalid_declaration": "施策の宣言を読めません（thth study-report <file> で形を確かめてください）",
    "declaration_out_of_scope": "宣言の account が、置く account と同じ持ち主（project）ではありません",
    "plaza_project_required": "project を持たない account は広場の参加・open に使えません",
    "invalid_visibility": "--visibility は open か project です",
    "redaction_unavailable": "他の人の情報を落とすための台帳が読めないので open に出していません"
                             "（project の範囲なら置けます）",
    "invalid_until": "--until は 2026-09-30T00:00:00+09:00 のような timezone 付きの時刻です",
    "nothing_to_update": "--refresh・--verdict・--visibility のどれかを付けてください",
    "plaza_hidden": "管理者が非表示にした書き込みです。返信・更新はできません",
    "already_hidden": "既に非表示です",
    "invalid_min_n": "--min-n は 1 以上の整数です",
    "invalid_goal": "--goal は reach（表示）・click（サイト誘導）・follow（フォロー）・reply（会話）の"
                    "どれかです（無ければ付けない）",
    "invalid_kind_detail": "--kind-detail は finding にだけ付けられ、pattern（型）・rule（規則）・"
                           "pitfall（罠）・tool_tip（道具のコツ）のどれかです",
    "scope_required": f"--scope に媒体・企画の範囲を 1 行（{SCOPE_NOTE_MAX} 字まで）で書いてください"
                      "（読み手が読者層の違いを知るため。例: Threads の朝の投稿・茶の話題）",
    "how_required": "施策（measure）には --how に数字を出し直せる thth の命令を 1 行書いてください"
                    "（例: thth measured kopicha-threads）",
    "invalid_how": f"--how は `thth ` で始まる 1 行（{HOW_MAX} 字まで）の命令です。道具は実行しません",
    "observed_is_tool_only": "observed（観測あり）は道具だけが付けます。--evidence-level は stated"
                             "（本文だけ）か hypothesis（見立て）です",
    "invalid_evidence_level": "--evidence-level は stated か hypothesis です",
    "invalid_trial_result": "追試（trial）には --result reproduced・not_reproduced・not_tried の"
                            "どれかを付けてください",
    "open_digest_mismatch": "他の持ち主に見える本文と観測が一段目のあとで変わりました。--confirm を外して"
                            "一段目からやり直し、表示を読み直してください",
    "open_requires_cli": "open にする（他の持ち主に見せる）のは人の CLI の二段確認だけです"
                         "（thth plaza post … --open / thth plaza update … --visibility open）。"
                         "MCP では project の範囲まで置けます",
    "third_party_handle": "open に出す文に @名前 の形（自分の handle 以外）があります。他の人の名前は"
                          "open に出せません。消してから置き直してください（project の範囲なら置けます）",
}

# **置くのは作業の一部と道具が言う**（設計 §5・報告の口 3.3.0 B と同じ型）。
WELCOME = ("施策を試したら広場に置き、次を決める前に他の媒体の施策を読む——"
           "どちらも利用者の作業の一部です。小さな気づきも")


class PlazaError(ValueError):
    """静的な理由コードだけを持つ断り（`REASONS` のどれか）。"""

    def __init__(self, reason, plaza_id=None):
        super().__init__(reason if reason in REASONS else "plaza_store_unavailable")
        self.plaza_id = plaza_id


# ------------------------------------------------------------------ 形

def _valid_reply(row) -> bool:
    return (isinstance(row, dict) and isinstance(row.get("text"), str)
            and row.get("kind") in REPLY_KINDS and jst.parse(row.get("at")) is not None
            and (row.get("account") is None or accounts.name_is_safe(row.get("account")))
            and (row.get("result") in TRIAL_RESULTS if row.get("kind") == "trial"
                 else row.get("result") is None))


def _valid(record) -> bool:
    """形の検査（壊れた 1 件で一覧全体を止めない・読めないものは数えて飛ばす）。"""
    if not isinstance(record, dict) or record.get("schema_version") != SCHEMA_VERSION:
        return False
    if not isinstance(record.get("plaza_id"), str) or not PLAZA_ID.match(record["plaza_id"]):
        return False
    if record.get("kind") not in KINDS or record.get("scope") not in SCOPES:
        return False
    if jst.parse(record.get("at")) is None:
        return False
    # 退出した持ち主の open の書き込みだけが account を持たない（設計 §6）。
    if record.get("account") is None:
        if not record.get("left_owner"):
            return False
    elif not accounts.name_is_safe(record.get("account")):
        return False
    project = record.get("project")
    if project is not None and (not isinstance(project, str) or not project):
        return False
    if not isinstance(record.get("title"), str) or not isinstance(record.get("body"), str):
        return False
    replies = record.get("replies")
    if not isinstance(replies, list) or not all(_valid_reply(row) for row in replies):
        return False
    if not isinstance(record.get("targets"), list) or not isinstance(record.get("observations"), list):
        return False
    verdict = record.get("verdict")
    if verdict is not None and (not isinstance(verdict, dict) or verdict.get("verdict") not in VERDICTS):
        return False
    # 施策の目的（設計 3.6.0 §A2）。3.5.0 までの書き込みは持たない（None）。
    if record.get("goal") is not None and record["goal"] not in _goals.GOALS:
        return False
    hidden = record.get("hidden")
    return hidden is None or isinstance(hidden, dict)


STORE = private_store.Store(
    DIRECTORY, id_key="plaza_id", id_pattern=PLAZA_ID, valid=_valid, error=PlazaError,
    unavailable="plaza_store_unavailable", not_found="plaza_not_found",
    log_unavailable="plaza_log_unavailable", max_bytes=MAX_FILE_BYTES,
    temporary_prefix=".plaza-")


def load_all():
    """読むだけの口（ロックは取らない）。置き場がまだ無ければ 0 件。"""
    return STORE.load()


# --------------------------------------------------------------- 参加

def _read_members(directory):
    """参加している持ち主（project → {at, by}）。無ければ空。壊れていれば置き場が読めない。"""
    if directory is None:
        return {}
    try:
        fd = os.open(MEMBERS_FILE, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise PlazaError("plaza_store_unavailable") from exc
    try:
        with os.fdopen(fd, "rb") as stream:
            import stat as _stat
            if not _stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("not_regular")
            value = json.loads(stream.read(MAX_FILE_BYTES + 1))
    except (OSError, ValueError, RecursionError) as exc:
        raise PlazaError("plaza_store_unavailable") from exc
    projects = value.get("projects") if isinstance(value, dict) else None
    if not isinstance(projects, dict) or any(
            not isinstance(key, str) or not isinstance(row, dict) for key, row in projects.items()):
        raise PlazaError("plaza_store_unavailable")
    return projects


def members():
    """参加している project の集合（読むだけ）。"""
    directory = STORE.open()
    try:
        return frozenset(_read_members(directory))
    finally:
        if directory is not None:
            os.close(directory)


def _project_of(target):
    """`join <project>` の対象を project 名に（account 名なら、その project）。"""
    if not isinstance(target, str) or not accounts.name_is_safe(target):
        raise PlazaError("plaza_project_required")
    try:
        names = accounts.list_account_names()
    except accounts.AccountError:
        raise PlazaError("account_unavailable") from None
    if target in names:
        try:
            project = accounts.load_account(target).get("project")
        except accounts.AccountError:
            raise PlazaError("account_unavailable") from None
        if not project:
            raise PlazaError("plaza_project_required")
        return project
    # 台帳に 1 本も無い project は参加させない（綴りの違いで空の持ち主を作らない）。
    for name in names:
        try:
            if accounts.load_account(name).get("project") == target:
                return target
        except accounts.AccountError:
            continue
    raise PlazaError("invalid_account")


def set_membership(target, *, joined, by, via="cli", now=None):
    """持ち主（project）の参加・退出（管理者だけ）。変更ログは `plaza_joined`・`plaza_left`。"""
    by = poster(by)
    if via not in VIAS:
        raise PlazaError("invalid_post")
    project = _project_of(target)
    at = jst.iso(now or jst.now_jst())
    with STORE.locked() as directory:
        current = _read_members(directory)
        before = dict(current)
        changed = (project in current) != joined
        if joined and project not in current:
            current[project] = {"at": at, "by": admin_log.clean(by)}
        elif not joined:
            current.pop(project, None)
        if changed:
            STORE.write(directory, {"schema_version": SCHEMA_VERSION, "projects": current},
                        name=MEMBERS_FILE)
            try:
                admin_log.append("plaza_joined" if joined else "plaza_left", project, {},
                                 by=by, via=via,
                                 diff={"plaza": ["absent", "present"] if joined
                                       else ["present", "absent"]})
            except private_store.LOG_ERRORS:
                STORE.write(directory, {"schema_version": SCHEMA_VERSION, "projects": before},
                            name=MEMBERS_FILE)
                raise PlazaError("plaza_log_unavailable") from None
    return {"schema_version": SCHEMA_VERSION, "report_type": "plaza_joined" if joined else "plaza_left",
            "project": project, "joined": joined, "changed": changed, "at": at}


# --------------------------------------------------------------- 見る人

class Viewer:
    """読む側（account → project）。**持ち主の単位は project**、project を持たない
    account は account 名でだけ当てる（`None` 同士を同じ持ち主とみなさない）。

    `admin=True` は管理者（全部をそのまま読む）。
    """

    def __init__(self, allowed=None, *, admin=False):
        allowed = dict(allowed or {})
        self.accounts = frozenset(allowed)
        self.projects = frozenset(p for p in allowed.values() if isinstance(p, str) and p)
        self.by_account = allowed
        self.admin = admin

    def owns(self, project, account) -> bool:
        if self.admin:
            return True
        if project is not None:
            return project in self.projects
        return account is not None and account in self.accounts


def owner_label(project, account):
    """他の持ち主に見せる名義（project 名。account 名・by は見せない）。"""
    return project or LEFT_LABEL


def access(record, viewer, joined_projects) -> str | None:
    """`own`（同じ持ち主・管理者）・`open`（参加した他の持ち主が読む写し）・`None`（読めない）。

    **open は、置いた持ち主と読む持ち主の両方が参加しているときだけ**。非表示は
    置いた持ち主（と管理者）にだけ見える。
    """
    if viewer.admin or viewer.owns(record.get("project"), record.get("account")):
        return "own"
    if record.get("scope") != "open" or record.get("hidden"):
        return None
    if record.get("project") not in joined_projects:
        return None
    if not (viewer.projects & joined_projects):
        return None
    return "open"


def viewer_for_target(target):
    """CLI の `<account|project>`（account 名が先）を読む側に。同じ project の全 account。"""
    if not isinstance(target, str) or not accounts.name_is_safe(target):
        raise PlazaError("invalid_account")
    try:
        names = accounts.list_account_names()
    except accounts.AccountError:
        raise PlazaError("account_unavailable") from None
    project = None
    if target in names:
        try:
            project = accounts.load_account(target).get("project")
        except accounts.AccountError:
            raise PlazaError("account_unavailable") from None
        if not project:
            return Viewer({target: None})
    else:
        project = target
    allowed = {}
    for name in names:
        try:
            cfg = accounts.load_account(name)
        except accounts.AccountError:
            continue
        if cfg.get("project") == project:
            allowed[name] = project
    if not allowed:
        raise PlazaError("invalid_account")
    return Viewer(allowed)


# ------------------------------------------------------------------ 検査

def _text(value, limit, *, required=True, one_line=False):
    return private_store.check_text(value, limit, error=PlazaError, invalid="invalid_post",
                                    too_long="post_too_long", required=required,
                                    one_line=one_line)


def poster(by):
    """誰が置いた・返した・判定したか（`admin_log.actor` と同じ検査）。"""
    try:
        return admin_log.actor(by)
    except ValueError:
        raise PlazaError("by_required") from None


def _no_secret(*values):
    if any(redact.looks_like_secret(value) for value in values if isinstance(value, str)):
        raise PlazaError("secret_detected")


def _digest(title, body):
    return hashlib.sha256((title + "\n" + body).encode("utf-8")).hexdigest()


def _new_id(now, directory):
    for _ in range(8):
        plaza_id = "p" + now.strftime("%Y%m%d") + "-" + secrets.token_hex(4)
        try:
            os.stat(plaza_id + ".json", dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            return plaza_id
    raise PlazaError("plaza_store_unavailable")


def _events_by(records, account, now):
    """直近 24 時間にこの account が置いた・返した件数（件数の上限）。"""
    n = 0
    for row in records:
        if row.get("account") == account and now - jst.parse(row["at"]) < WINDOW:
            n += 1
        for reply_row in row["replies"]:
            if reply_row.get("account") == account and now - jst.parse(reply_row["at"]) < WINDOW:
                n += 1
    return n


def _until(value):
    if value is None:
        return None
    at = jst.parse(value) if isinstance(value, str) else None
    if at is None:
        raise PlazaError("invalid_until")
    return jst.iso(at)


def _check_declaration(value, now):
    from . import study_report
    try:
        return study_report.validate_declaration(value, now)
    except (study_report.StudyError, TypeError, ValueError, KeyError):
        raise PlazaError("invalid_declaration") from None


def load_declaration_file(path, now=None):
    """CLI の `--declaration <file>`（study-report と同じ読み方・同じ検査）。"""
    from . import study_report
    try:
        return study_report.load_declaration(path, now or jst.now_jst())
    except (study_report.StudyError, TypeError, ValueError):
        raise PlazaError("invalid_declaration") from None


def _owner_accounts(account, project, trusted_accounts=None):
    """置く account と同じ持ち主の account → 媒体。

    `trusted_accounts`（サーバの口: credential が許した account → project）が
    あれば、その中だけ。CLI は台帳から同じ project の account を集める。
    """
    result = {}
    if trusted_accounts is not None:
        candidates = [name for name, value in trusted_accounts.items()
                      if (value == project if project else name == account)]
    else:
        try:
            candidates = accounts.list_account_names()
        except accounts.AccountError:
            raise PlazaError("account_unavailable") from None
    for name in candidates:
        try:
            cfg = accounts.load_account(name)
        except accounts.AccountError:
            continue
        if (cfg.get("project") == project) if project else name == account:
            result[name] = cfg.get("media")
    return result


def _scope_note(value):
    """媒体・企画の範囲（必須・1 行・120 字）。読み手が読者層の違いを知るため（§9-5）。"""
    if not (isinstance(value, str) and value.strip()):
        raise PlazaError("scope_required")
    return _text(value, SCOPE_NOTE_MAX, one_line=True)


def _kind_detail(kind, value):
    """finding の細目（任意）。finding 以外には付けない。"""
    if value is None:
        return None
    if kind != "finding" or value not in KIND_DETAILS:
        raise PlazaError("invalid_kind_detail")
    return value


def _how(value, *, required):
    """数字を出し直せる thth の命令（§9-3）。**形だけを検査し、実行はしない。**"""
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise PlazaError("how_required")
        return None
    if not isinstance(value, str):
        raise PlazaError("invalid_how")
    value = value.strip()
    if ("\n" in value or "\r" in value or len(value) > HOW_MAX or not value.startswith("thth ")
            or any(ord(c) < 32 or c == "\x7f" for c in value)):
        raise PlazaError("invalid_how")
    return value


def _declared_level(value):
    """人や LLM が名乗れる印（stated・hypothesis）。observed は道具だけ（§9-4）。"""
    if value is None:
        return "stated"
    if value == "observed":
        raise PlazaError("observed_is_tool_only")
    if value not in DECLARABLE_LEVELS:
        raise PlazaError("invalid_evidence_level")
    return value


def open_view(record):
    """他の持ち主に見える 1 件（写しだけ）。二段確認の一段目に出し、digest の元にする。"""
    from . import plaza_observe
    copy = record.get("open_copy") or {}
    history = record.get("observations") or []
    return {"kind": record["kind"], "kind_detail": record.get("kind_detail"),
            "evidence_level": evidence_level_of(record),
            "owner": owner_label(record.get("project"), record.get("account")),
            "medium": record.get("medium"),
            **{key: copy.get(key) for key in ("title", "body", "hypothesis", "change",
                                              "scope_note", "how", "verdict_reason")},
            "verdict": (record.get("verdict") or {}).get("verdict"),
            "observation": plaza_observe._columns(history[-1], "open") if history else None,
            "masked": copy.get("masked", 0)}


def open_digest(record):
    """他の持ち主に見える中身の digest（先頭 20 桁）。一段目と二段目で同じ中身かを確かめる。"""
    raw = json.dumps(open_view(record), ensure_ascii=False, sort_keys=True, allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _open_gate(record, confirm):
    """open にする二段確認。1 回目は見える中身と digest を返す（何も書かない）。"""
    digest = open_digest(record)
    if confirm != digest:
        if confirm is None:
            return {"schema_version": SCHEMA_VERSION, "report_type": "plaza_open_preview",
                    "opened": False, "digest": digest, "plaza_id": record.get("plaza_id"),
                    "account": record.get("account"), "visible_to_other_owners": open_view(record)}
        raise PlazaError("open_digest_mismatch")
    return None


def evidence_level_of(record):
    """見立てと事実の印。**道具が付けた観測の列が 1 つでも取れていれば observed**、
    それ以外は置いた人が名乗った印（stated か hypothesis）。"""
    history = record.get("observations") or []
    if history and any(column.get("observed") for column in history[-1].get("columns") or []):
        return "observed"
    declared = record.get("declared_level")
    return declared if declared in DECLARABLE_LEVELS else "stated"


# ------------------------------------------------------------------ 置く

def post(account, *, kind, title, body, by, scope_note=None, kind_detail=None, how=None,
         evidence_level="stated", declarations=(), hypothesis=None, change=None,
         until=None, min_n=5, visibility="project", via="cli", project=None, medium=None,
         now=None, trusted_accounts=None, confirm=None, goal=None):
    """広場に 1 件置く。`plaza_id` を返す。

    **open は二段確認**（masaru 裁定 09-23・approve と同じ線）: `visibility="open"` の
    1 回目（`confirm` 無し）は、他人の情報を落とした後の「他の持ち主に見える本文と
    観測」と digest を返すだけで何も置かない。同じ中身で `confirm=<digest>` の 2 回目に
    初めて置く。中身が変わっていたら `open_digest_mismatch`。

    `trusted_accounts` はサーバの口（credential が account と project を決めた）。
    CLI は台帳から project と媒体を読む。**秘密が混ざっていたら置かない。**
    measure なら、宣言（study-report の形）ごとに道具が観測を付ける。
    """
    from . import plaza_observe, plaza_redact
    by = poster(by)
    if via not in VIAS:
        raise PlazaError("invalid_post")
    if not accounts.name_is_safe(account):
        raise PlazaError("invalid_account")
    if kind not in KINDS:
        raise PlazaError("invalid_kind")
    if visibility not in SCOPES:
        raise PlazaError("invalid_visibility")
    if type(min_n) is not int or min_n < 1:
        raise PlazaError("invalid_min_n")
    title = _text(title, TITLE_MAX, one_line=True)
    body = _text(body, BODY_MAX)
    scope_note = _scope_note(scope_note)
    kind_detail = _kind_detail(kind, kind_detail)
    # 施策の目的（設計 3.6.0 §A2・任意）。媒体をまたいで「reach の型は…」を比べる札。
    if goal is not None and goal not in _goals.GOALS:
        raise PlazaError("invalid_goal")
    how = _how(how, required=kind == "measure")
    declared_level = _declared_level(evidence_level)
    hypothesis = _text(hypothesis, TEXT_MAX, required=False)
    change = _text(change, TEXT_MAX, required=False)
    until = _until(until)
    now = now or jst.now_jst()
    if trusted_accounts is None:
        try:
            cfg = accounts.load_account(account)
        except accounts.AccountError:
            raise PlazaError("account_unavailable") from None
        project, medium = cfg.get("project"), cfg.get("media")
        admin_log.register_account_secrets(cfg, via="cli")
    declarations = list(declarations or [])
    if kind != "measure" and declarations:
        raise PlazaError("not_a_measure")
    if len(declarations) > DECLARATIONS_MAX:
        raise PlazaError("post_too_long")
    owners = _owner_accounts(account, project, trusted_accounts) if declarations else {}
    targets = []
    for value in declarations:
        declaration = _check_declaration(value, now)
        if declaration["account"] not in owners:
            raise PlazaError("declaration_out_of_scope")
        targets.append({"account": declaration["account"], "medium": owners[declaration["account"]],
                        "declaration": declaration})
    if kind == "measure" and hypothesis is None and targets:
        hypothesis = targets[0]["declaration"]["hypothesis"]
    if kind == "measure" and change is None and targets:
        change = targets[0]["declaration"]["change"]
    _no_secret(title, body, hypothesis, change, by, scope_note, how,
               *(json.dumps(t["declaration"], ensure_ascii=False) for t in targets))
    title, body = private_store.fold_paths(title), private_store.fold_paths(body)
    scope_note = private_store.fold_paths(scope_note)
    hypothesis, change = private_store.fold_paths(hypothesis), private_store.fold_paths(change)
    if visibility == "open":
        if not project:
            raise PlazaError("plaza_project_required")
        if project not in members():
            raise PlazaError("plaza_not_joined")
    allowed = tuple(trusted_accounts) if trusted_accounts is not None else None
    observations = []
    if targets:
        observations.append(plaza_observe.observe(targets, now=now, min_n=min_n, by=by,
                                                  trigger="posted", allowed_names=allowed))
    record = {"schema_version": SCHEMA_VERSION, "plaza_id": None, "at": jst.iso(now),
              "updated_at": jst.iso(now), "kind": kind, "scope": visibility,
              "title": title, "body": body, "hypothesis": hypothesis, "change": change,
              "kind_detail": kind_detail, "scope_note": scope_note, "how": how,
              "declared_level": declared_level, "evidence_level": None,
              "project": project, "account": account, "medium": medium, "by": by, "via": via,
              "tool_version": __version__, "targets": targets, "until": until, "min_n": min_n,
              "observations": observations, "verdict": None, "replies": [], "hidden": None,
              "open_copy": None, "goal": goal}
    record["evidence_level"] = evidence_level_of(record)
    if visibility == "open":
        # 他人の情報を落とした写しを**置く時点で**作る（読む側は写しだけを見る）。
        plaza_redact.attach_open_copy(record, trusted_accounts=trusted_accounts)
        preview = _open_gate(record, confirm)
        if preview is not None:
            return preview
    digest = _digest(title, body)
    with STORE.locked() as directory:
        records, _broken = STORE.load_all(directory)
        # 重複は**同じ account の中だけ**で見る（他の持ち主の id を返さない）。
        for row in records:
            if (row.get("account") == account and now - jst.parse(row["at"]) < WINDOW
                    and _digest(row["title"], row["body"]) == digest):
                raise PlazaError("duplicate_post", plaza_id=row["plaza_id"])
        if _events_by(records, account, now) >= DAILY_LIMIT:
            raise PlazaError("plaza_rate_limited")
        record["plaza_id"] = _new_id(now, directory)
        STORE.create(directory, record, lambda _record: admin_log.append(
            "plaza_posted", account, {"media": medium}, by=by, via=via,
            diff={"plaza": ["absent", "present"], "scope": [None, visibility]}))
    return {"schema_version": SCHEMA_VERSION, "report_type": "plaza_posted",
            "plaza_id": record["plaza_id"], "kind": kind, "scope": visibility,
            "at": record["at"], "account": account, "project": project,
            "n_targets": len(targets), "observed": bool(observations),
            "evidence_level": record["evidence_level"], "tool_version": __version__}


# ------------------------------------------------------------------ 読む

def _preview(text):
    from . import threads_read_cli
    return threads_read_cli._one_line(text or "", PREVIEW_CHARS)


def _reply_view(reply_row, viewer, joined, records_by_id):
    """返信 1 件の見せ方。**他の持ち主が書いた返信は写し（open_text）だけ・名義は project。**"""
    mine = viewer.admin or viewer.owns(reply_row.get("project"), reply_row.get("account"))
    link = reply_row.get("measure_id")
    if link is not None:
        linked = records_by_id.get(link)
        if linked is None or access(linked, viewer, joined) is None:
            link = None
    if mine:
        return {"at": reply_row["at"], "kind": reply_row["kind"], "text": reply_row["text"],
                "by": reply_row.get("by"), "account": reply_row.get("account"),
                "medium": reply_row.get("medium"), "owner": owner_label(reply_row.get("project"),
                                                                         reply_row.get("account")),
                "measure_id": link, "result": reply_row.get("result"), "own": True}
    return {"at": reply_row["at"], "kind": reply_row["kind"],
            "text": reply_row.get("open_text") or "",
            "owner": owner_label(reply_row.get("project"), reply_row.get("account")),
            "medium": reply_row.get("medium"), "measure_id": link,
            "result": reply_row.get("result"), "own": False}


def summary_row(record, level, viewer=None) -> dict:
    """一覧の 1 行（本文・返信の本文は出さない）。`level` は `own` か `open`。"""
    replies = record["replies"]
    if level == "open":
        copy = record.get("open_copy") or {}
        title = copy.get("title") or ""
        return {"plaza_id": record["plaza_id"], "at": record["at"], "kind": record["kind"],
                "scope": "open", "title": title,
                "owner": owner_label(record.get("project"), record.get("account")),
                "medium": record.get("medium"), "n_replies": len(replies),
                "last_reply_at": replies[-1]["at"] if replies else None,
                "verdict": (record.get("verdict") or {}).get("verdict"),
                "kind_detail": record.get("kind_detail"),
                "evidence_level": evidence_level_of(record),
                "scope_note": copy.get("scope_note"), "trials": trial_counts(record),
                "goal": record.get("goal"), "view": "open"}
    return {"plaza_id": record["plaza_id"], "at": record["at"], "kind": record["kind"],
            "scope": record["scope"], "title": record["title"],
            "owner": owner_label(record.get("project"), record.get("account")),
            "project": record.get("project"), "account": record.get("account"),
            "medium": record.get("medium"), "by": record.get("by"),
            "n_replies": len(replies), "last_reply_at": replies[-1]["at"] if replies else None,
            "verdict": (record.get("verdict") or {}).get("verdict"),
            "kind_detail": record.get("kind_detail"), "evidence_level": evidence_level_of(record),
            "scope_note": record.get("scope_note"), "trials": trial_counts(record),
            "goal": record.get("goal"),
            "hidden": bool(record.get("hidden")), "view": "own"}


def list_posts(viewer, *, open_only=False):
    """広場の一覧。既定は自分の持ち主の書き込み（project と自分の open）。

    `open_only=True` は open の広場（参加した持ち主の open・自分のものを含む）。
    **不参加の持ち主は open を読めない**（`plaza_not_joined`）。
    """
    records, broken = load_all()
    joined = members()
    if open_only and not viewer.admin and not (viewer.projects & joined):
        raise PlazaError("plaza_not_joined")
    rows = []
    for record in records:
        level = access(record, viewer, joined)
        if level is None:
            continue
        if open_only and record["scope"] != "open":
            continue
        if not open_only and level != "own":
            continue
        rows.append(summary_row(record, level))
    rows.sort(key=lambda row: (row["at"], row["plaza_id"]), reverse=True)
    return {"schema_version": SCHEMA_VERSION, "report_type": "plaza_list",
            "filter": "open" if open_only else "own", "n": len(rows), "posts": rows,
            "joined": bool(viewer.projects & joined) if not viewer.admin else None,
            # 壊れた件数は管理者にだけ（他の持ち主の件数の手掛かりを渡さない）。
            "unreadable": broken if viewer.admin else None}


def text_numbers(text):
    """本文に人や LLM が書いた数字（**観測ではない**・道具は確かめていない）。"""
    return _NUMBER.findall(text or "")[:20]


_NUMBER = re.compile(r"[+\-−]?\d+(?:[.,]\d+)*\s*(?:%|％|倍|件|回|人|pt|ポイント)?")


def show(plaza_id, viewer):
    """1 件の全文と返信・観測・媒体をまたぐ比較の表。読めない id と無い id は同じ断り。"""
    from . import plaza_observe
    records, _broken = load_all()
    records_by_id = {row["plaza_id"]: row for row in records}
    record = records_by_id.get(plaza_id) if isinstance(plaza_id, str) else None
    if record is None:
        # 壊れていて一覧から落ちた 1 件も、無い id と同じに見せる（在ることを漏らさない）。
        raise PlazaError("plaza_not_found")
    joined = members()
    level = access(record, viewer, joined)
    if level is None:
        raise PlazaError("plaza_not_found")
    replies = [_reply_view(row, viewer, joined, records_by_id) for row in record["replies"]]
    linked = []
    for row in record["replies"]:
        target = (records_by_id.get(row.get("measure_id"))
                  if row.get("kind") in ("tried", "trial") else None)
        if target is not None:
            target_level = access(target, viewer, joined)
            if target_level is not None and target["plaza_id"] not in {r["plaza_id"] for r, *_ in linked}:
                linked.append((target, target_level, row["kind"]))
    if level == "open":
        copy = record.get("open_copy") or {}
        payload = {"report_type": "plaza_post", "view": "open", "plaza_id": record["plaza_id"],
                   "at": record["at"], "kind": record["kind"], "scope": "open",
                   "title": copy.get("title") or "", "body": copy.get("body") or "",
                   "hypothesis": copy.get("hypothesis"), "change": copy.get("change"),
                   "scope_note": copy.get("scope_note"), "how": copy.get("how"),
                   "kind_detail": record.get("kind_detail"),
                   "evidence_level": evidence_level_of(record),
                   "owner": owner_label(record.get("project"), record.get("account")),
                   "medium": record.get("medium"), "until": record.get("until"),
                   "verdict": plaza_observe.verdict_view(record.get("verdict"), level, record),
                   "goal": record.get("goal"), "masked": copy.get("masked", 0)}
    else:
        payload = {"report_type": "plaza_post", "view": "own", **{
            key: record.get(key) for key in (
                "plaza_id", "at", "updated_at", "kind", "scope", "title", "body", "hypothesis",
                "change", "project", "account", "medium", "by", "via", "tool_version", "until",
                "min_n", "hidden", "kind_detail", "scope_note", "how", "declared_level",
                "goal")},
            "evidence_level": evidence_level_of(record),
            "owner": owner_label(record.get("project"), record.get("account")),
            "verdict": plaza_observe.verdict_view(record.get("verdict"), level, record),
            "open_copy": ({"masked": (record.get("open_copy") or {}).get("masked", 0)}
                          if record["scope"] == "open" else None)}
    payload["replies"] = replies
    payload["n_replies"] = len(replies)
    # 追試（§9-2）: 再現した・再現しなかった・試していないを**同じ重さで**数える（分母つき）。
    payload["trials"] = trial_counts(record)
    # **観測は道具が付けたものだけ**。本文の数字は「本文」として別の欄（設計 §6）。
    payload["observation"] = plaza_observe.observation_view(record, level)
    payload["observation_reason"] = plaza_observe.observation_reason(record)
    payload["comparison"] = plaza_observe.comparison_table(record, level, linked)
    payload["text_numbers"] = {"source": "text", "verified": False,
                               "values": text_numbers(payload["body"])}
    return payload


# ---------------------------------------------------------------- 返信

def reply(plaza_id, *, account, kind, text=None, measure_id=None, result=None, by, viewer,
          via="cli", now=None, trusted_accounts=None):
    """返信を 1 つ足す（`comment`・`tried`〔自分の measure へのリンク必須〕・`agree`・
    `disagree`〔理由必須〕）。**読めない 1 件には返せない**（無い id と同じ断り）。
    """
    from . import plaza_redact
    by = poster(by)
    if via not in VIAS:
        raise PlazaError("invalid_post")
    if kind not in REPLY_KINDS:
        raise PlazaError("invalid_reply_kind")
    if not accounts.name_is_safe(account) or account not in viewer.accounts:
        raise PlazaError("invalid_account")
    if kind in REPLY_TEXT_REQUIRED and not (isinstance(text, str) and text.strip()):
        raise PlazaError("reason_required" if kind == "disagree" else "invalid_post")
    text = _text(text, REPLY_MAX, required=kind in REPLY_TEXT_REQUIRED) or ""
    if kind == "tried" and not measure_id:
        raise PlazaError("measure_link_required")
    if kind not in ("tried", "trial") and measure_id is not None:
        raise PlazaError("measure_link_invalid")
    # 追試（§9-2）は結果が必須。本文（note）とリンク（自分の measure）は任意。
    if kind == "trial" and result not in TRIAL_RESULTS:
        raise PlazaError("invalid_trial_result")
    if kind != "trial" and result is not None:
        raise PlazaError("invalid_trial_result")
    _no_secret(text, by)
    text = private_store.fold_paths(text)
    project = viewer.by_account.get(account)
    medium = None
    try:
        cfg = accounts.load_account(account)
        medium = cfg.get("media")
        if trusted_accounts is None:
            project = cfg.get("project")
    except accounts.AccountError:
        raise PlazaError("account_unavailable") from None
    now = now or jst.now_jst()
    at = jst.iso(now)
    records, _broken = load_all()
    joined = members()
    if measure_id is not None:
        linked = next((row for row in records if row["plaza_id"] == measure_id), None)
        if (linked is None or linked["kind"] != "measure"
                or not Viewer({account: project}).owns(linked.get("project"), linked.get("account"))):
            raise PlazaError("measure_link_invalid")
    row = {"at": at, "by": by, "account": account, "project": project, "medium": medium,
           "kind": kind, "text": text, "measure_id": measure_id, "via": via}
    if kind == "trial":
        row["result"] = result

    def change(record):
        level = access(record, viewer, joined)
        if level is None:
            raise PlazaError("plaza_not_found")
        if record.get("hidden"):
            raise PlazaError("plaza_hidden")
        if _events_by([record] + [r for r in records if r["plaza_id"] != record["plaza_id"]],
                      account, now) >= DAILY_LIMIT:
            raise PlazaError("plaza_rate_limited")
        if record["scope"] == "open":
            # open の 1 件への返信は、書いた持ち主の台帳で他人の情報を落とした写しを持つ。
            row["open_text"] = plaza_redact.open_text(text, account, project,
                                                      trusted_accounts=trusted_accounts)
        record["replies"].append(row)
        record["updated_at"] = at

    record = STORE.update(plaza_id, change, lambda record: admin_log.append(
        "plaza_replied", account, {"media": medium}, by=by, via=via,
        diff={"reply": ["absent", "present"], "kind": [None, kind]}))
    return {"schema_version": SCHEMA_VERSION, "report_type": "plaza_replied",
            "plaza_id": record["plaza_id"], "kind": kind, "result": result,
            "n_replies": len(record["replies"]), "trials": trial_counts(record), "at": at}


# ---------------------------------------------------------------- 更新

def update(plaza_id, *, account, by, viewer, refresh=False, verdict=None, reason=None,
           visibility=None, via="cli", now=None, trusted_accounts=None, confirm=None):
    """結果の更新（観測の取り直し）・判定・範囲の変更。**置いた持ち主だけ。**

    project から open に切り替えるときは二段確認（`post` と同じ・`confirm`）。"""
    from . import plaza_observe, plaza_redact
    by = poster(by)
    if via not in VIAS:
        raise PlazaError("invalid_post")
    if not accounts.name_is_safe(account) or account not in viewer.accounts:
        raise PlazaError("invalid_account")
    if not refresh and verdict is None and visibility is None:
        raise PlazaError("nothing_to_update")
    if verdict is not None and verdict not in VERDICTS:
        raise PlazaError("invalid_verdict")
    if visibility is not None and visibility not in SCOPES:
        raise PlazaError("invalid_visibility")
    if verdict is not None:
        if not (isinstance(reason, str) and reason.strip()):
            raise PlazaError("reason_required")
        reason = _text(reason, REASON_MAX)
        _no_secret(reason)
        reason = private_store.fold_paths(reason)
    _no_secret(by)
    now = now or jst.now_jst()
    at = jst.iso(now)
    joined = members()
    # 読む・観測を付ける仕事はロックの外で（study-report の計算は台帳を読むだけ）。
    current = STORE.get(plaza_id)
    if access(current, viewer, joined) is None:
        raise PlazaError("plaza_not_found")
    if not viewer.owns(current.get("project"), current.get("account")):
        raise PlazaError("not_owner")
    if current.get("hidden"):
        raise PlazaError("plaza_hidden")
    if (refresh or verdict is not None) and current["kind"] != "measure":
        raise PlazaError("not_a_measure")
    if visibility == "open":
        if not current.get("project"):
            raise PlazaError("plaza_project_required")
        if current["project"] not in joined:
            raise PlazaError("plaza_not_joined")
    fresh = None
    if refresh and current["targets"]:
        allowed = tuple(trusted_accounts) if trusted_accounts is not None else None
        fresh = plaza_observe.observe(current["targets"], now=now, min_n=current.get("min_n") or 5,
                                      by=by, trigger="refresh", allowed_names=allowed)
    diff = {}

    def change(record):
        if not viewer.owns(record.get("project"), record.get("account")):
            raise PlazaError("not_owner")
        if record.get("hidden"):
            raise PlazaError("plaza_hidden")
        if fresh is not None:
            history = record["observations"] + [fresh]
            if len(history) > OBSERVATION_HISTORY_MAX:
                # 置いた時点の 1 件は残す（後から数字が変わったことが分かるように）。
                history = history[:1] + history[-(OBSERVATION_HISTORY_MAX - 1):]
            record["observations"] = history
            diff["observation"] = ["present", "present"]
        if verdict is not None:
            old = (record.get("verdict") or {}).get("verdict")
            record["verdict"] = {"at": at, "by": by, "verdict": verdict, "reason": reason}
            diff["verdict"] = [old, verdict]
        if visibility is not None and visibility != record["scope"]:
            diff["scope"] = [record["scope"], visibility]
            record["scope"] = visibility
        record["evidence_level"] = evidence_level_of(record)
        if record["scope"] == "open":
            plaza_redact.attach_open_copy(record, trusted_accounts=trusted_accounts)
        else:
            # project に戻したら写しを捨てる（他の持ち主からは見えない）。
            record["open_copy"] = None
            for obs in record["observations"]:
                obs.pop("open_columns", None)
        record["updated_at"] = at

    opening = visibility == "open" and current["scope"] != "open"
    if opening and confirm is None:
        # 一段目: 写しに置き換えた後の中身を見せるだけ（置き場には書かない）。
        preview_record = json.loads(json.dumps(current))
        change(preview_record)
        diff.clear()
        return _open_gate(preview_record, None)

    def change_checked(record):
        switching = visibility == "open" and record["scope"] != "open"
        change(record)
        if switching:
            _open_gate(record, confirm)

    record = STORE.update(plaza_id, change_checked, lambda record: admin_log.append(
        "plaza_updated", account, {"media": record.get("medium")}, by=by, via=via,
        diff=diff or {"plaza": ["present", "present"]}))
    return {"schema_version": SCHEMA_VERSION, "report_type": "plaza_updated",
            "plaza_id": record["plaza_id"], "scope": record["scope"],
            "verdict": (record.get("verdict") or {}).get("verdict"),
            "n_observations": len(record["observations"]), "at": at,
            "refreshed": fresh is not None}


# ---------------------------------------------------------------- 管理者

def hide(plaza_id, *, by, reason, via="cli", now=None):
    """問題のある書き込みを非表示にする（理由つき・変更ログ `plaza_hidden`）。"""
    by = poster(by)
    if via not in VIAS:
        raise PlazaError("invalid_post")
    if not (isinstance(reason, str) and reason.strip()):
        raise PlazaError("reason_required")
    reason = _text(reason, REASON_MAX)
    _no_secret(reason)
    at = jst.iso(now or jst.now_jst())

    def change(record):
        if record.get("hidden"):
            raise PlazaError("already_hidden")
        record["hidden"] = {"at": at, "by": by, "reason": reason}
        record["updated_at"] = at

    record = STORE.update(plaza_id, change, lambda record: admin_log.append(
        "plaza_hidden", record.get("account") or record.get("project") or "plaza",
        {"media": record.get("medium")}, by=by, via=via, diff={"hidden": ["absent", "present"]}))
    return {"schema_version": SCHEMA_VERSION, "report_type": "plaza_hidden",
            "plaza_id": record["plaza_id"], "hidden": record["hidden"]}


def admin_list():
    records, broken = load_all()
    rows = [summary_row(record, "own") for record in records]
    rows.sort(key=lambda row: (row["at"], row["plaza_id"]), reverse=True)
    joined = sorted(members())
    return {"schema_version": SCHEMA_VERSION, "report_type": "plaza_list", "filter": "all",
            "n": len(rows), "posts": rows, "joined_projects": joined, "unreadable": broken}


# ---------------------------------------------------------------- 退出

def purge_account(account, *, keep_open=False, by="leave", now=None):
    """退出（`account leave`）で、その account が置いた書き込みを消す（設計 §6）。

    - project 範囲の書き込みと返信は消す。
    - open の書き込みと返信は、`keep_open=True` なら「退出した持ち主」の名義で残す
      （account 名・by・宣言の投稿 ID・観測の account を落とす）。既定は消す。
    何度呼んでも同じ結果（退出の再試行で呼び直される）。置き場が無ければ何もしない。
    """
    if not accounts.name_is_safe(account):
        raise PlazaError("invalid_account")
    records, _broken = load_all()
    touched = any(row.get("account") == account
                  or any(r.get("account") == account for r in row["replies"]) for row in records)
    if not touched:
        return {"removed": 0, "anonymized": 0, "replies_removed": 0, "replies_anonymized": 0}
    at = jst.iso(now or jst.now_jst())
    counts = {"removed": 0, "anonymized": 0, "replies_removed": 0, "replies_anonymized": 0}
    with STORE.locked() as directory:
        records, _broken = STORE.load_all(directory)
        for record in records:
            changed = False
            if record.get("account") == account:
                if record["scope"] == "open" and keep_open:
                    _anonymize(record)
                    counts["anonymized"] += 1
                    changed = True
                else:
                    STORE.remove(directory, record["plaza_id"])
                    counts["removed"] += 1
                    continue
            kept = []
            for row in record["replies"]:
                if row.get("account") != account:
                    kept.append(row)
                    continue
                changed = True
                if record["scope"] == "open" and keep_open:
                    row.update(account=None, by=LEFT_LABEL, measure_id=None, text=row.get("open_text") or "",
                               left_owner=True)
                    kept.append(row)
                    counts["replies_anonymized"] += 1
                else:
                    counts["replies_removed"] += 1
            record["replies"] = kept
            if changed:
                record["updated_at"] = at
                STORE.write(directory, record)
        try:
            admin_log.append("plaza_updated", account, {}, by=by, via="cli",
                             diff={"account_left": ["present", "absent"],
                                   "counts": [None, [counts[k] for k in sorted(counts)]]})
        except private_store.LOG_ERRORS:
            # 消した事実は戻せない（退出は戻さない）。記録だけ無いことを言う。
            raise PlazaError("plaza_log_unavailable") from None
    return counts


def _anonymize(record):
    """退出した持ち主の open の書き込み: 名義と宣言の中身を落とし、写しを本文にする。"""
    copy = record.get("open_copy") or {}
    record.update(account=None, by=LEFT_LABEL, left_owner=True,
                  title=copy.get("title") or "", body=copy.get("body") or "",
                  hypothesis=copy.get("hypothesis"), change=copy.get("change"),
                  scope_note=copy.get("scope_note"), how=copy.get("how"))
    if record.get("verdict"):
        record["verdict"].update(by=LEFT_LABEL, reason=copy.get("verdict_reason"))
    for target in record["targets"]:
        target["account"] = None
        target["declaration"] = None
    for obs in record["observations"]:
        obs["columns"] = obs.get("open_columns") or []
        obs["by"] = LEFT_LABEL


# ---------------------------------------------------------- observe に載せる

def observe_summary(viewer, *, since, now, exclude_account=None):
    """`thth observe` の 0 段「広場: 新着 n（project）・open の新着 m」。

    - 新着 = `since` より後（`now` 以前）に置かれた書き込みと返信の数。
    - project の新着は**自分の持ち主の書き込み**に付いたもの、open の新着は**他の
      持ち主の open の書き込み**に付いたもの（読めるものだけ）。
    - `exclude_account`（account 1 本を対象にした observe）の書いたものは数えない
      （自分が書いたものは新着ではない）。
    - 参加していなければ open は null と理由 `plaza_not_joined`。
    """
    try:
        records, _broken = load_all()
        joined = members()
    except PlazaError as error:
        return {"project_new": None, "project_denominator": None, "open_new": None,
                "open_denominator": None, "open_reason": None, "since": jst.iso(since),
                "cannot_say": str(error)}
    project_new = project_total = open_new = open_total = 0
    for record in records:
        level = access(record, viewer, joined)
        if level is None:
            continue
        mine = viewer.owns(record.get("project"), record.get("account"))
        events = [(record["at"], record.get("account"))] + [
            (row["at"], row.get("account")) for row in record["replies"]]
        n = sum(1 for at, author in events
                if since < jst.parse(at) <= now and author != exclude_account)
        if mine:
            project_total += 1
            project_new += n
        elif level == "open":
            open_total += 1
            open_new += n
    participating = bool(viewer.projects & joined)
    return {"project_new": project_new, "project_denominator": project_total,
            "open_new": open_new if participating else None,
            "open_denominator": open_total if participating else None,
            "open_reason": None if participating else "plaza_not_joined",
            "since": jst.iso(since), "cannot_say": None}


def observe_steps(viewer, names_media, *, since, now):
    """`next_steps` の `kind: "plaza"`（候補の列挙だけ・本文は作らない）。

    - `read_replies`: 自分の account が置いた施策に、`since` より後に他の人の返信が付いた。
    - `verdict`: 自分の account が置いた施策で、判定がまだ無く、期間（until）を過ぎた
      （期間が無ければ判定待ち）。
    - `try_on_medium`: 判定が付いた同じ持ち主の施策を、まだ試していない媒体（account）。
      試したかどうかは、その施策の宣言の account と、`tried` の返信でつながった施策の
      宣言の account で決める。
    """
    try:
        records, _broken = load_all()
        joined = members()
    except PlazaError:
        return []
    by_id = {row["plaza_id"]: row for row in records}
    steps = []
    names = set(names_media)
    for record in records:
        if record["kind"] != "measure" or record.get("hidden"):
            continue
        if access(record, viewer, joined) != "own":
            continue
        title = _preview(record["title"])
        if record.get("account") in names:
            fresh = [row for row in record["replies"]
                     if row.get("account") not in names and since < jst.parse(row["at"]) <= now]
            if fresh:
                steps.append({"kind": "plaza", "candidate": "read_replies",
                              "plaza_id": record["plaza_id"], "account": record["account"],
                              "title": title, "n_new_replies": len(fresh)})
            until = jst.parse(record.get("until")) if record.get("until") else None
            if record.get("verdict") is None and (until is None or until <= now):
                steps.append({"kind": "plaza", "candidate": "verdict",
                              "plaza_id": record["plaza_id"], "account": record["account"],
                              "title": title, "until": record.get("until")})
        if record.get("verdict") is not None:
            tried = tried_accounts(record, by_id)
            for name in sorted(names):
                if name in tried or not viewer.owns(viewer.by_account.get(name), name):
                    continue
                if viewer.by_account.get(name) != record.get("project"):
                    continue
                steps.append({"kind": "plaza", "candidate": "try_on_medium",
                              "plaza_id": record["plaza_id"], "account": name,
                              "medium": names_media.get(name), "title": title,
                              "verdict": record["verdict"]["verdict"]})
    return steps


def tried_accounts(record, by_id):
    """その施策を試した account（宣言の account と、`tried` でつながった施策の宣言の account）。"""
    found = {target.get("account") for target in record["targets"] if target.get("account")}
    for row in record["replies"]:
        # 「うちでも試した」と、再現した・しなかった追試（試していない追試は数えない）。
        if not (row.get("kind") == "tried" or row.get("kind") == "trial"
                and row.get("result") in ("reproduced", "not_reproduced")):
            continue
        linked = by_id.get(row.get("measure_id"))
        if linked is None:
            continue
        found |= {target.get("account") for target in linked["targets"] if target.get("account")}
    return found


def trial_counts(record):
    """追試の数（§9-2）。**再現しなかった報告を再現した報告と同じ重さで数える**（分母つき）。"""
    counts = {result: 0 for result in TRIAL_RESULTS}
    for row in record.get("replies") or []:
        if row.get("kind") == "trial" and row.get("result") in counts:
            counts[row["result"]] += 1
    return {**counts, "denominator": sum(counts.values())}


def recent_trial(viewer):
    """「最近追試が付いた書き込み 1 件」（設計 §9-6）。読める範囲（自分の持ち主の書き込みと、
    参加していれば open の写し）で、いちばん新しい追試が付いた 1 件。無ければ None。"""
    records, _broken = load_all()
    joined = members()
    best = None
    for record in records:
        level = access(record, viewer, joined)
        if level is None or record.get("hidden"):
            continue
        trials = [row for row in record["replies"] if row.get("kind") == "trial"]
        if not trials:
            continue
        latest = max(trials, key=lambda row: jst.parse(row["at"]))
        if best is None or jst.parse(latest["at"]) > jst.parse(best[1]["at"]):
            best = (record, latest, level)
    if best is None:
        return None
    record, latest, level = best
    title = record["title"] if level == "own" else (record.get("open_copy") or {}).get("title") or ""
    return {"plaza_id": record["plaza_id"], "title": _preview(title), "at": latest["at"],
            "result": latest["result"], "trials": trial_counts(record), "view": level}
