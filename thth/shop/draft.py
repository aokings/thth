"""店の違いから queue の下書きを作る（**承認されていない形**・設計 §2「出すのは承認のあとだけ」）。

- まとめて作らない: 1 回に 1 商品（`--item`）か、種類を選んで上限 `MAX_DRAFTS` 本。
- 作るのは「公開で買える」商品の `new`（と `published`）・`restock`・`price_down` だけ。売り切れ・値上げ・
  非公開・消えた商品の下書きは作らない。
- 本文は素の 1〜2 行（商品名・「新しく入りました」などの一言・URL）。文は利用者の LLM が書く前提。
  値下げの「前 → 今」の数は本文に書かず、頭（front-matter）に材料として置くだけ。
- 頭に出所: `from_store: store/<店>/item/<id>`・読んだ時刻・違いの種類。商品名は利用者の入力なので、改行・
  制御文字・`---` を落としてから入れる（`base.clean_text`）。
- 商品頁の URL は公式で組み方を確かめられなかったので、店の URL（`GET /api/user` の `shop_url`）を載せ、
  `from_store_url: shop` と書く。画像は今の queue の画像の欄（repo の中のファイル）に素直に乗らないので載せず、
  `from_store_image: none` と書く。
- 書く前に今の lint（`thth/lint.py`）に通す。同じ名前のファイルがあれば上書きしない。commit・push はしない
  （レーンの LLM と人がする）。
- **同じ違いから 2 本目を作らない**（監査 5.15.0 直したほうがよい 3）: 頭に比べた写しの時刻
  （`from_store_since`）を置き、queue に同じ出所・同じ違い・同じ写しの下書きがあれば `store_draft_exists`。
  写しを進めたあと（`diff --accept`）の同じ商品の違いは、別の違いとして作れる。
- 承認のときに読み直すための材料として、下書きを作ったときの値段（`from_store_price`）を頭に置く
  （本文には書かない・`recheck.py`）。
- 商品名の頭の `#` は落とす（`## …` だと本文の 1 行目が節の見出しと読まれる）。名前が空になる商品は作らない。
"""
from __future__ import annotations

import datetime
import os
import secrets
from pathlib import Path

from .. import accounts, jst
from . import base
from .diff import DRAFT_KINDS
from .wire import StoreRefused

MAX_DRAFTS = 3
DRAFT_NAME_MAX = 80
PHRASES = {"new": "新しく入りました", "restock": "再入荷しました", "price_down": "お値下げしました"}


def queue_dir(cfg) -> Path:
    try:
        repo = Path(cfg["repo_dir"]).absolute()
        relative = Path(cfg["queue_dir"])
    except (KeyError, TypeError):
        raise StoreRefused("store_draft_account", "queue_dir") from None
    if relative.is_absolute() or not relative.parts or any(p.startswith(".") for p in relative.parts):
        raise StoreRefused("store_draft_account", "queue_dir")
    queue = repo / relative
    if os.path.commonpath([repo, queue.absolute()]) != str(repo) or not queue.is_dir():
        raise StoreRefused("store_draft_account", "queue_dir")
    return queue


def choose(changes: list, rows: list, *, item=None, kind=None) -> list:
    """下書きにする `(違い, 行)` の組（上限 `MAX_DRAFTS`）。"""
    by_id = {row["id"]: row for row in rows}
    picked = []
    for change in changes:
        draft_kind = DRAFT_KINDS.get(change["kind"])
        row = by_id.get(change["id"])
        if draft_kind is None or row is None or not base.buyable(row) or not display_name(row):
            continue
        if item is not None and change["id"] != item:
            continue
        if kind is not None and draft_kind != kind:
            continue
        picked.append((dict(change, draft_kind=draft_kind), row))
    return picked[:1] if item is not None else picked[:MAX_DRAFTS]


def display_name(row: dict) -> str:
    """本文に載せる商品名（改行・制御文字・`---` を落とし、頭の `#` も落とす）。空なら作らない。"""
    return base.clean_text(row.get("name"), DRAFT_NAME_MAX).lstrip("#＃ ").strip()


def origin(store: str, row_id) -> str:
    return f"store/{store}/item/{row_id}"


def material(change: dict) -> str | None:
    if change.get("draft_kind") == "price_down":
        return f"price {change.get('before')} -> {change.get('after')}"
    return None


def text_for(store: str, account: str, media: str, change: dict, row: dict, *, shop_url: str, read_at: str,
             publish_at: str, since=None) -> str:
    kind = change["draft_kind"]
    name = display_name(row)
    lines = ["---", "thth: 1", f"account: {account}", "status: draft", f"publish_at: {publish_at}",
             f"from_store: {origin(store, row['id'])}", f"from_store_read_at: {read_at}",
             f"from_store_change: {change['kind']}", "from_store_url: shop", "from_store_image: none"]
    if isinstance(since, str) and since:
        lines.append(f"from_store_since: {base.clean_text(since, 40)}")
    if isinstance(row.get("price"), int) and not isinstance(row.get("price"), bool):
        lines.append(f"from_store_price: {row['price']}")
    if material(change):
        lines.append(f"from_store_material: {material(change)}")
    lines += ["---", "", f"## {media}", "", f"{name} {PHRASES[kind]}", shop_url, ""]
    return "\n".join(lines)


def file_name(store: str, row_id, kind: str, now) -> str:
    return f"{jst.to_jst(now):%Y-%m-%d}-store-{store}-{row_id}-{kind}.md"


def publish_at(now) -> str:
    """仮の時刻（翌日の 12:00 JST）。承認の前に人と LLM が直す。"""
    day = jst.to_jst(now).date() + datetime.timedelta(days=1)
    return jst.iso(datetime.datetime.combine(day, datetime.time(12, 0), tzinfo=jst.JST))


def _head(text: str) -> dict:
    """頭（front-matter）の `from_store*` の行だけを素朴に読む（queue の全部を解析しない）。"""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}
    out = {}
    for line in lines[1:200]:
        if line.strip() == "---":
            break
        key, sep, value = line.partition(":")
        if sep and key.startswith("from_store"):
            out[key.strip()] = value.strip()
    return out


def same_change_exists(queue: Path, store: str, row_id, change: dict, since) -> str | None:
    """同じ出所・同じ違い・同じ写しから作った下書きが queue にあれば、そのファイルの名前。"""
    want = {"from_store": origin(store, row_id), "from_store_change": change["kind"],
            "from_store_since": base.clean_text(since, 40) if isinstance(since, str) else ""}
    wanted_material = material(change)
    marker = "from_store: " + want["from_store"]
    for path in sorted(queue.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if marker not in text:
            continue
        head = _head(text)
        if (head.get("from_store") == want["from_store"] and head.get("from_store_change") == want["from_store_change"]
                and head.get("from_store_since", "") == want["from_store_since"]
                and head.get("from_store_material") == wanted_material):
            return path.name
    return None


def write(queue: Path, name: str, text: str) -> str:
    """lint に通してから、同じ名前が無いときだけ書く。"""
    from .. import lint
    target = queue / name
    if target.exists():
        raise StoreRefused("store_draft_exists")
    temp = queue / (".lint-" + secrets.token_hex(8) + ".tmp")
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        problems = [p for p in lint.lint_file(str(temp)) if not lint.is_warning(p)]
    finally:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass
    if problems:
        raise StoreRefused("store_draft_rejected", lint.reason_code(problems) or "lint")
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        raise StoreRefused("store_draft_exists") from None
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    return str(target)


def load_account(name: str) -> dict:
    try:
        return accounts.load_account(name)
    except (accounts.AccountError, ValueError):
        raise StoreRefused("store_draft_account", "account") from None
