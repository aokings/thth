"""観測の地図（設計 3.5.0）の置き場——点（観測軸）と線（包含）。

動機（設計 §0）: 話題を「点」、話題どうしのつながりを「線」として持ち、同じ点に
自分の投稿・広場の書き込み・（有効にしたときだけ）世間の集計を重ねる。

規律:

  (a) **置き場は VM の私有**（`$THTH_ROOT/state/_map/<project>/`・0600・ディレクトリは
      0700）。骨は報告の口・広場と共通（`thth/private_store.py`）。`state/` は git に
      入らない（`.gitignore`）。SNS の台帳には書かない。
  (b) **点は人だけが足す・消す**（`thth admin map node add|remove --by`）。道具が
      他人の投稿から語を拾って点を増やすことはしない（他人の文から作った派生データを
      持たない・設計 §1・§5）。project あたり 20 点まで。
  (c) **点に @ハンドル・個人名らしき語・URL を入れさせない**（照合 §6-2: 特定の人の
      監視にしない）。敬称の付いた語・「名 姓」のラテン文字・16 桁の仮名（author_key の
      形）も断る。敬称の無い個人名は形からは見分けられない（未確認のまま残る穴）。
  (d) **線（包含）も人が張る**（`thth admin map edge add <狭い語> <広い語> --by`）。
      両端は既にある点・自分自身への線と輪は作らない。
  (e) 変更ログは presence-only（点の語は記録に書かない・監視語と同じ）。ログに
      書けなければ変更を戻す。
"""
from __future__ import annotations

import json
import os
import re
import unicodedata

from . import accounts, admin_log, jst, private_store, redact

SCHEMA_VERSION = 1
DIRECTORY = "_map"
CONFIG_FILE = "map.json"
# 1 project の点の上限（設計 §2「上限は project あたり 20 点」）。世間の層を有効に
# したとき 1 日の検索は点の数 × 媒体なので、ここが 1 日の要求数の上限にもなる。
MAX_NODES = 20
# 包含の線の上限（点 20 の 3 倍。包含は木に近い形なので、これを超える形は想定しない）。
MAX_EDGES = 60
# 点の長さは監視語と同じ（`accounts.WATCH_WORD_MAX_CHARS`）。
NODE_MAX_CHARS = accounts.WATCH_WORD_MAX_CHARS
# 保持（設計 §2・照合 §6-4）。**半年の推移で話題を選ぶ**のが目的なので 180 日。
# これより長くは入れさせない（目的に必要な間だけ・T §3(d)(i)2(a)）。
DEFAULT_RETENTION_DAYS = 180
MAX_RETENTION_DAYS = 180
MAX_FILE_BYTES = 8 * 1024 * 1024
VIAS = ("cli", "mcp")

REASONS = frozenset((
    "by_required", "invalid_project", "project_unknown", "invalid_node", "node_handle",
    "node_url", "node_person_like", "secret_detected", "node_exists", "node_not_found",
    "node_limit", "invalid_edge", "edge_exists", "edge_not_found", "edge_cycle", "edge_limit",
    "invalid_retention", "invalid_range", "invalid_since", "map_store_unavailable",
    "map_log_unavailable", "scope_unavailable",
))

NEXT = {
    "by_required": "--by <名前> を付けてください（誰が変えたかを残します）",
    "invalid_project": "project 名を確かめてください（台帳の project の綴り）",
    "project_unknown": "その project の account が台帳にありません（綴りを確かめてください）",
    "invalid_node": f"点は空でない 1 行・{NODE_MAX_CHARS} 字までの語です（制御文字は使えません）",
    "node_handle": "点に @名前 や仮名（author_key）の形は入れられません。特定の人を観測する"
                   "地図にはしません",
    "node_url": "点に URL やドメインは入れられません。話題の語にしてください",
    "node_person_like": "点に個人名らしき語（敬称つき・「名 姓」の形）は入れられません。"
                        "話題の語にしてください",
    "secret_detected": "秘密らしき値が含まれます。点には話題の語だけを入れてください",
    "node_exists": "その点は既にあります（thth map show <project>）",
    "node_not_found": "その点はありません（thth map show <project>）",
    "node_limit": f"点は project あたり {MAX_NODES} までです。使わない点を消してから足してください",
    "invalid_edge": "線の両端は既にある別々の点です（thth admin map node add で先に点を足す）",
    "edge_exists": "その線は既にあります",
    "edge_not_found": "その線はありません（thth map show <project>）",
    "edge_cycle": "包含の線が輪になります（広い語が狭い語に含まれる形は張れません）",
    "edge_limit": f"包含の線は project あたり {MAX_EDGES} 本までです",
    "invalid_retention": f"保持の日数は 1〜{MAX_RETENTION_DAYS} の整数です",
    "invalid_range": "--from・--to は 2026-09-01 の形の日付です（--from は --to より前）",
    "invalid_since": "--since は期間（30d・12w）か ISO 時刻です",
    "map_store_unavailable": "地図の置き場が読めません。管理者に知らせてください",
    "map_log_unavailable": "変更ログに書けなかったので変えていません。管理者に知らせてください",
    "scope_unavailable": "その project の地図は、この credential では読めません",
}


class MapError(ValueError):
    """静的な理由コードだけを持つ断り（`REASONS` のどれか）。"""

    def __init__(self, reason):
        super().__init__(reason if reason in REASONS else "map_store_unavailable")


def _never(_record):
    return False


def store(project) -> private_store.Store:
    """project の置き場（`state/_map/<project>/`）。`project` は検査済みの名前だけ。"""
    if not accounts.name_is_safe(project):
        raise MapError("invalid_project")
    return private_store.Store(
        f"{DIRECTORY}/{project}", id_key="project", id_pattern=re.compile(r"(?!)"),
        valid=_never, error=MapError, unavailable="map_store_unavailable",
        not_found="map_store_unavailable", log_unavailable="map_log_unavailable",
        max_bytes=MAX_FILE_BYTES, temporary_prefix=".map-")


# ------------------------------------------------------------------ 持ち主

def project_accounts(target):
    """`<project>`（account 名ならその project）→ `(project, {account: 台帳})`。

    台帳に 1 本も無い project は断る（綴りの違いで空の地図を作らない・広場の参加と同じ）。
    読めない台帳は飛ばす。
    """
    if not isinstance(target, str) or not accounts.name_is_safe(target):
        raise MapError("invalid_project")
    try:
        names = accounts.list_account_names()
    except accounts.AccountError:
        raise MapError("map_store_unavailable") from None
    project = target
    if target in names:
        try:
            project = accounts.load_account(target).get("project")
        except accounts.AccountError:
            raise MapError("project_unknown") from None
        if not project or not accounts.name_is_safe(project):
            raise MapError("invalid_project")
    found = {}
    for name in names:
        try:
            cfg = accounts.load_account(name)
        except accounts.AccountError:
            continue
        if cfg.get("project") == project:
            found[name] = cfg
    if not found:
        raise MapError("project_unknown")
    return project, found


# -------------------------------------------------------------------- 点

# 敬称（語尾）。「〇〇さん」「〇〇先生」は特定の人を指す（照合 §6-2）。「先生」だけの語は
# 話題として通す（前に何かが付いたときだけ断る）。「模様」「源氏」のような普通の語で
# 当たる「様」「氏」は入れない。
_HONORIFIC = re.compile(r".(さん|さま|サマ|くん|ちゃん|先生|せんせい|選手|議員|容疑者|被告)$")
# 「名 姓」のラテン文字（John Smith の形）。
_LATIN_NAME = re.compile(r"^[A-Z][a-z]+(?:[ .'-][A-Z][a-z]+)+$")
# URL・ドメイン。
_URL = re.compile(r"(?i)(?:[a-z][a-z0-9+.-]*://|www\.|\b[a-z0-9-]+\.(?:com|net|org|jp|io|app|"
                  r"social|me|co|dev|xyz|info|ly|to|so|ai|tv|fm|link|page|site|blog)\b)")
# 仮名（`adapters.base.author_key` の形・16 進 16 桁）。
_AUTHOR_KEY = re.compile(r"(?i)(?<![0-9a-f])[0-9a-f]{16}(?![0-9a-f])")


def normalize_node(word):
    """点の語の検査と正規化（前後の空白と先頭の `#` を落とす・topic と同じ）。

    `queuefile.normalize_topic()` と同じ落とし方にしておくと、自分の層（投稿の
    `topic`）とそのまま突き合わせられる。
    """
    if not isinstance(word, str):
        raise MapError("invalid_node")
    value = unicodedata.normalize("NFC", word).strip().lstrip("#＃").strip()
    if not value or len(value) > NODE_MAX_CHARS:
        raise MapError("invalid_node")
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise MapError("invalid_node")
    if "@" in value or "＠" in value or _AUTHOR_KEY.search(value):
        raise MapError("node_handle")
    if _URL.search(value):
        raise MapError("node_url")
    if _HONORIFIC.search(value) or _LATIN_NAME.match(value):
        raise MapError("node_person_like")
    if redact.looks_like_secret(value):
        raise MapError("secret_detected")
    return value


def node_key(word):
    """点の同一性（大文字小文字と全角半角を畳む）。"""
    return unicodedata.normalize("NFKC", word).casefold()


# ------------------------------------------------------------------ 設定

def empty_config(project):
    return {"schema_version": SCHEMA_VERSION, "project": project, "nodes": [], "edges": [],
            "retention_days": DEFAULT_RETENTION_DAYS, "updated_at": None}


def _valid_config(value, project) -> bool:
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        return False
    if value.get("project") != project:
        return False
    nodes, edges = value.get("nodes"), value.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list) or len(nodes) > MAX_NODES:
        return False
    words = set()
    for row in nodes:
        if not isinstance(row, dict) or not isinstance(row.get("word"), str):
            return False
        words.add(row["word"])
    for row in edges:
        if (not isinstance(row, dict) or row.get("narrower") not in words
                or row.get("broader") not in words):
            return False
    days = value.get("retention_days")
    return type(days) is int and 1 <= days <= MAX_RETENTION_DAYS


def _read_config(project_store, directory, project):
    if directory is None:
        return empty_config(project)
    data = project_store.read_raw(directory, CONFIG_FILE)
    if data is None:
        return empty_config(project)
    try:
        value = json.loads(data)
    except (ValueError, RecursionError):
        raise MapError("map_store_unavailable") from None
    if not _valid_config(value, project):
        raise MapError("map_store_unavailable")
    return value


def load_config(project):
    """地図の形（点・線・保持）。読むだけ。置き場が無ければ空の地図。"""
    project_store = store(project)
    directory = project_store.open()
    try:
        return _read_config(project_store, directory, project)
    finally:
        if directory is not None:
            os.close(directory)


def _actor(by):
    try:
        return admin_log.actor(by)
    except ValueError:
        raise MapError("by_required") from None


def _change(project, event, change, *, by, via, now=None, diff=None):
    """設定を書き換えて変更ログに残す。ログに書けなければ元に戻す。"""
    by = _actor(by)
    if via not in VIAS:
        raise MapError("invalid_project")
    project_store = store(project)
    with project_store.locked() as directory:
        config = _read_config(project_store, directory, project)
        before = json.loads(json.dumps(config))
        result = change(config)
        config["updated_at"] = jst.iso(now or jst.now_jst())
        project_store.write(directory, config, name=CONFIG_FILE)
        try:
            admin_log.append(event, project, {}, by=by, via=via, diff=diff(before, config))
        except private_store.LOG_ERRORS:
            project_store.write(directory, before, name=CONFIG_FILE)
            raise MapError("map_log_unavailable") from None
    return config, result


def _find(config, word):
    key = node_key(word)
    for row in config["nodes"]:
        if node_key(row["word"]) == key:
            return row
    return None


def _counts(before, after):
    return {"nodes": [len(before["nodes"]), len(after["nodes"])],
            "edges": [len(before["edges"]), len(after["edges"])]}


def add_node(target, word, *, by, via="cli", now=None):
    """点を 1 つ足す（人だけ・`--by` 必須・上限 20）。"""
    by = _actor(by)
    project, _names = project_accounts(target)
    word = normalize_node(word)
    at = jst.iso(now or jst.now_jst())

    def change(config):
        if _find(config, word) is not None:
            raise MapError("node_exists")
        if len(config["nodes"]) >= MAX_NODES:
            raise MapError("node_limit")
        config["nodes"].append({"word": word, "at": at, "by": admin_log.clean(by)})

    config, _ = _change(project, "map_node_added", change, by=by, via=via, now=now,
                        diff=lambda b, a: {"map_node": ["absent", "present"], **_counts(b, a)})
    return {"schema_version": SCHEMA_VERSION, "report_type": "map_node_added",
            "project": project, "node": word, "n_nodes": len(config["nodes"]),
            "max_nodes": MAX_NODES, "at": at}


def remove_node(target, word, *, by, via="cli", now=None):
    """点を 1 つ消す。その点に掛かる包含の線と、その点の集計の行も消す（持ち続けない）。"""
    by = _actor(by)
    project, _names = project_accounts(target)
    if not isinstance(word, str):
        raise MapError("invalid_node")
    removed = {}

    def change(config):
        row = _find(config, word.strip().lstrip("#＃").strip())
        if row is None:
            raise MapError("node_not_found")
        config["nodes"] = [node for node in config["nodes"] if node is not row]
        kept = [edge for edge in config["edges"]
                if row["word"] not in (edge["narrower"], edge["broader"])]
        removed["edges"] = len(config["edges"]) - len(kept)
        removed["word"] = row["word"]
        config["edges"] = kept

    config, _ = _change(project, "map_node_removed", change, by=by, via=via, now=now,
                        diff=lambda b, a: {"map_node": ["present", "absent"], **_counts(b, a)})
    from . import map_world
    rows = map_world.forget_node(project, removed["word"])
    return {"schema_version": SCHEMA_VERSION, "report_type": "map_node_removed",
            "project": project, "node": removed["word"], "n_nodes": len(config["nodes"]),
            "edges_removed": removed["edges"], "rows_removed": rows}


def _ancestors(edges, word):
    """`word` を含む（より広い）点の集合（包含の線を辿る）。"""
    seen, stack = set(), [word]
    while stack:
        current = stack.pop()
        for edge in edges:
            if edge["narrower"] == current and edge["broader"] not in seen:
                seen.add(edge["broader"])
                stack.append(edge["broader"])
    return seen


def add_edge(target, narrower, broader, *, by, via="cli", now=None):
    """包含の線を 1 本張る（`narrower ⊂ broader`・人だけ・`--by` 必須）。"""
    by = _actor(by)
    project, _names = project_accounts(target)
    at = jst.iso(now or jst.now_jst())
    chosen = {}

    def change(config):
        small = _find(config, narrower) if isinstance(narrower, str) else None
        large = _find(config, broader) if isinstance(broader, str) else None
        if small is None or large is None or small is large:
            raise MapError("invalid_edge")
        a, b = small["word"], large["word"]
        if any(edge["narrower"] == a and edge["broader"] == b for edge in config["edges"]):
            raise MapError("edge_exists")
        if a in _ancestors(config["edges"], b):
            raise MapError("edge_cycle")
        if len(config["edges"]) >= MAX_EDGES:
            raise MapError("edge_limit")
        config["edges"].append({"narrower": a, "broader": b, "at": at,
                                "by": admin_log.clean(by)})
        chosen.update(narrower=a, broader=b)

    config, _ = _change(project, "map_edge_added", change, by=by, via=via, now=now,
                        diff=lambda b, a: {"map_edge": ["absent", "present"], **_counts(b, a)})
    return {"schema_version": SCHEMA_VERSION, "report_type": "map_edge_added",
            "project": project, "edge": {"narrower": chosen["narrower"], "broader": chosen["broader"],
                                         "kind": "broader"},
            "n_edges": len(config["edges"]), "at": at}


def remove_edge(target, narrower, broader, *, by, via="cli", now=None):
    by = _actor(by)
    project, _names = project_accounts(target)
    chosen = {}

    def change(config):
        small = _find(config, narrower) if isinstance(narrower, str) else None
        large = _find(config, broader) if isinstance(broader, str) else None
        kept = [edge for edge in config["edges"]
                if not (small and large and edge["narrower"] == small["word"]
                        and edge["broader"] == large["word"])]
        if len(kept) == len(config["edges"]):
            raise MapError("edge_not_found")
        config["edges"] = kept
        chosen.update(narrower=small["word"], broader=large["word"])

    config, _ = _change(project, "map_edge_removed", change, by=by, via=via, now=now,
                        diff=lambda b, a: {"map_edge": ["present", "absent"], **_counts(b, a)})
    return {"schema_version": SCHEMA_VERSION, "report_type": "map_edge_removed",
            "project": project, "edge": {"narrower": chosen["narrower"], "broader": chosen["broader"],
                                         "kind": "broader"},
            "n_edges": len(config["edges"])}
