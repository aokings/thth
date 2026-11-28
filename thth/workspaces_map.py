"""媒体 → 括り（THTH アカウント）の対応表 `thth.workspaces/1` を読む・確かめる・書く。

正本は VM の 1 枚（既定 `/srv/thth/workspaces.json`、環境変数 `THTH_WS_MAP` で替えられる）。
形の約束は docs/約束_括りの対応表_thth.workspaces.1.md。hub と h2d は同じ形を自分で読む
（この部品を import しない）。3 つの部品が同じ答えを出すことは contracts/ の見本で確かめる。

読み方の決まり（約束 §3）:
- ファイルが無い → 全部の媒体を `main` として扱う（今の本番と同じ）。
- ファイルが壊れている → WorkspacesMapError。黙って `main` に倒さない。
- 読むたびに開く。覚えを作らない。
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

SCHEMA = "thth.workspaces/1"
DEFAULT_PATH = "/srv/thth/workspaces.json"
ENV_PATH = "THTH_WS_MAP"
FALLBACK_WS = "main"

# 媒体の名前は h2d の owner の形（小文字・数字・`-`）にそろえる。
MEDIUM_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
WS_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
OWNER_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
LEAD_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}(/[a-z0-9][a-z0-9-]{0,63})?$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

TOP_KEYS = {"schema", "updated_at", "unlisted", "workspaces", "media"}
WS_KEYS_REQUIRED = {"owner", "lead"}
WS_KEYS = WS_KEYS_REQUIRED | {"created"}

# 秘密らしい値（台帳に入れない約束）。値の文字列にこの形があれば断る。
SECRET_HINTS = re.compile(
    r"(?i)(sk-[a-z0-9]|ghp_|gho_|xox[abp]-|AKIA[0-9A-Z]{8}|-----BEGIN|bearer\s|"
    r"token|secret|passw|@[a-z0-9-]+\.[a-z])"
)


class WorkspacesMapError(ValueError):
    """対応表が読めない・形が違う。新しく書く操作は断り、管理者に知らせる。"""


@dataclass(frozen=True)
class WorkspacesMap:
    unlisted: str | None
    workspaces: Mapping[str, Mapping[str, str]]
    media: Mapping[str, str]
    present: bool  # ファイルがあったか（無ければ全部 main の既定）

    def workspace_of(self, medium: str) -> str | None:
        """媒体の括り。表に無ければ `unlisted`（None は「どの括りでもない・断る」）。"""
        if not isinstance(medium, str) or not MEDIUM_RE.match(medium):
            return None
        if medium in self.media:
            return self.media[medium]
        return self.unlisted

    def same_workspace(self, a: str, b: str) -> bool:
        """2 つの媒体が同じ括りにいるか。どちらかが括りを持たなければ False。"""
        wa, wb = self.workspace_of(a), self.workspace_of(b)
        return wa is not None and wa == wb

    def owner_of(self, ws: str) -> str | None:
        entry = self.workspaces.get(ws)
        return None if entry is None else entry.get("owner")

    def lead_of(self, ws: str) -> str | None:
        entry = self.workspaces.get(ws)
        return None if entry is None else entry.get("lead")


DEFAULT_MAP = WorkspacesMap(
    unlisted=FALLBACK_WS,
    workspaces={FALLBACK_WS: {"owner": "masaru", "lead": "media-hub"}},
    media={},
    present=False,
)


def map_path() -> Path:
    return Path(os.environ.get(ENV_PATH) or DEFAULT_PATH)


def validate(data: Any) -> list[str]:
    """形の誤りを全部返す（空なら正しい）。"""
    errs: list[str] = []
    if not isinstance(data, dict):
        return ["一番外が JSON の object でない"]
    extra = set(data) - TOP_KEYS
    if extra:
        errs.append(f"知らない欄: {sorted(extra)}")
    if data.get("schema") != SCHEMA:
        errs.append(f"schema が {SCHEMA!r} でない: {data.get('schema')!r}")
    ua = data.get("updated_at")
    if ua is not None and not isinstance(ua, str):
        errs.append("updated_at が文字列でない")

    wss = data.get("workspaces")
    if not isinstance(wss, dict) or not wss:
        errs.append("workspaces が空か object でない")
        wss = {}
    for name, entry in wss.items():
        if not WS_RE.match(str(name)):
            errs.append(f"括りの名の形が違う: {name!r}")
        if not isinstance(entry, dict):
            errs.append(f"workspaces.{name} が object でない")
            continue
        missing = WS_KEYS_REQUIRED - set(entry)
        if missing:
            errs.append(f"workspaces.{name} に {sorted(missing)} が無い")
        unknown = set(entry) - WS_KEYS
        if unknown:
            errs.append(f"workspaces.{name} に知らない欄: {sorted(unknown)}")
        if "owner" in entry and not (isinstance(entry["owner"], str) and OWNER_RE.match(entry["owner"])):
            errs.append(f"workspaces.{name}.owner の形が違う")
        if "lead" in entry and not (isinstance(entry["lead"], str) and LEAD_RE.match(entry["lead"])):
            errs.append(f"workspaces.{name}.lead の形が違う")
        if "created" in entry and not (isinstance(entry["created"], str) and DATE_RE.match(entry["created"])):
            errs.append(f"workspaces.{name}.created が YYYY-MM-DD でない")

    if "unlisted" not in data:
        errs.append("unlisted が無い（\"<括り>\" か null）")
    unl = data.get("unlisted")
    # 文字列でない値は `in` で引かずに誤りとして返す（配列・object だと TypeError で落ちていた・監査 5.32.0 軽い 10）。
    if unl is not None and not isinstance(unl, str):
        errs.append(f"unlisted が文字列か null でない: {type(unl).__name__}")
    elif unl is not None and unl not in wss:
        errs.append(f"unlisted の先が workspaces に無い: {unl!r}")
    if len(wss) >= 2 and unl is not None:
        errs.append("括りが 2 つ以上なのに unlisted が null でない（表に無い媒体が混ざる）")

    media = data.get("media")
    if not isinstance(media, dict):
        errs.append("media が object でない")
        media = {}
    for m, ws in media.items():
        if not MEDIUM_RE.match(str(m)):
            errs.append(f"媒体の名前の形が違う: {m!r}")
        if not isinstance(ws, str):
            errs.append(f"media.{m} が文字列でない: {type(ws).__name__}")
        elif ws not in wss:
            errs.append(f"media.{m} の先が workspaces に無い: {ws!r}")

    for path, value in _strings(data):
        if SECRET_HINTS.search(value):
            errs.append(f"秘密らしい値がある: {path}")
    return errs


def _strings(obj: Any, prefix: str = ""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _strings(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _strings(v, f"{prefix}[{i}]")
    elif isinstance(obj, str):
        yield prefix, obj


def parse(data: Any) -> WorkspacesMap:
    errs = validate(data)
    if errs:
        raise WorkspacesMapError("対応表が読めない・管理者に知らせる: " + " / ".join(errs))
    return WorkspacesMap(
        unlisted=data["unlisted"],
        workspaces={k: dict(v) for k, v in data["workspaces"].items()},
        media=dict(data["media"]),
        present=True,
    )


def load(path: str | os.PathLike | None = None) -> WorkspacesMap:
    """読むたびに開く。無ければ全部 main、壊れていれば WorkspacesMapError。"""
    p = Path(path) if path is not None else map_path()
    try:
        raw = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return DEFAULT_MAP
    except OSError as exc:
        raise WorkspacesMapError(f"対応表が読めない・管理者に知らせる: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise WorkspacesMapError(f"対応表が読めない・管理者に知らせる: JSON でない（{exc}）") from exc
    return parse(data)


def write(data: Mapping[str, Any], path: str | os.PathLike | None = None) -> Path:
    """形を確かめ、別の名前で書いてから付け替える（途中の姿を読ませない）。"""
    errs = validate(data)
    if errs:
        raise WorkspacesMapError("書かない: " + " / ".join(errs))
    p = Path(path) if path is not None else map_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".workspaces.", suffix=".json", dir=str(p.parent))
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write("\n")
    os.chmod(tmp, 0o644)
    os.replace(tmp, p)
    return p
