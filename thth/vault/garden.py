"""`thth garden`: 店の写しから静的な頁を作り、出す前に検査し、monban の庭に置く（段 1・**仮**・masaru の答えを待たずに
推しで作った・朝に覆せる）。

正本: monban の設計案 §14・§15.3・§15.6（庭の口と制限）、THTH の docs/原則_庭の来訪者と広告_2026-11-12.md（7 つ）。

作る頁（`build`）:

- `index.html`（店の頁 1 枚）・`items.html`（商品の一覧）・`style.css`。UTF-8・script なし・form なし・`http-equiv` なし・
  外の資源なし（画像も外の URL を読まない。BASE の画像を手元に取り込むのは段 2）。
- 載せるのは**公開で買える商品だけ**（`thth.shop.base.buyable`: 公開・販売中・在庫あり）。非公開の商品は写しに id しか無く、
  頁には何も出さない。商品の頁の URL は BASE の公式で確かめられていないので、買う道は店の URL（`--shop-url`）へのリンク。
- 無料版の「THTH の案内」の静的な枠を各頁に 1 つ（原則の 7。段 1 には有料版が無いので、いつも入れる）。枠の印は
  `data-thth-guide="free"`。

出す前の検査（`check_dir`・publish の前に必ず通す）:

- monban の Edge の制限（edge/src/vault.js の写し）: 種類・名前・2000 個・1 個 25 MB・全部で 100 MB・HTML/SVG/XML は
  1 枚 1 MB・UTF-8 だけ（BOM の FE FF・FF FE と NUL を断る）・`http-equiv` を含めない。monban の道具が黙って飛ばす
  点で始まる名前と symlink は、THTH は飛ばさずに断る（出したつもりの物が出ないのを防ぐ）。
- THTH の柵: タグの中の script・form・iframe など・`on…=` の差し込み・`javascript:`、外の資源（`<a>` のリンクを除く、
  `http:`・`https:`・`//` で始まる属性の値・CSS の外の `url(…)` と `@import`）、案内の枠が 1 つでない HTML。
  script と form は monban の CSP でも止まるが、止まった頁は壊れて見えるので、出す前に前向きに断る。

二段確認（構想 §6・monban §14.2）: `publish` の一段目は「新しく出る・変わる・消える」ファイルの一覧と digest を見せる
だけで何も置かない。二段目（`--confirm <digest>`）で monban-vault garden publish を呼ぶ。止める（`stop`）は確認なし。
最後に置いた目録（道と SHA-256 だけ・公開の中身）は `$THTH_ROOT/garden_state/` に 1 枚だけ持つ（違いを見せるため）。
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re

from .. import accounts, secrets_fs
from . import tools, wire
from .sync import URL as VAULT_URL
from .wire import VaultRefused

# ------------------------------------------------------------------ monban の制限（edge/src/vault.js の写し）

TYPES = frozenset(("html", "css", "png", "jpg", "jpeg", "webp", "gif", "svg", "ico", "woff2", "txt", "xml"))
TEXT_KINDS = frozenset(("html", "svg", "xml"))
PATH = re.compile(r"(?:[A-Za-z0-9_~-][A-Za-z0-9._~-]{0,127}/){0,15}[A-Za-z0-9_~-][A-Za-z0-9._~-]{0,127}\Z")
FILES_MAX = 2000
FILE_MAX = 25 * 1024 * 1024
GARDEN_MAX = 100 * 1024 ** 2
HTML_MAX = 1024 * 1024
GARDEN_NAME = re.compile(r"[a-z0-9](?:[a-z0-9]|-(?!-)){0,30}\Z")
GARDEN_RESERVED = frozenset(("www", "login", "owner", "monban", "admin", "api", "mail", "oauth", "mcp", "thth",
                             "static", "assets"))

# ------------------------------------------------------------------ THTH の柵

GUIDE_MARK = 'data-thth-guide="free"'
_TAG = re.compile(r"<\s*([A-Za-z][A-Za-z0-9:_-]*)((?:[^>\"']|\"[^\"]*\"|'[^']*')*)>", re.DOTALL)
_ATTR = re.compile(r"([A-Za-z_:][A-Za-z0-9_:.-]*)\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s\"'>]+)")
ACTIVE_TAGS = frozenset(("script", "form", "iframe", "frame", "frameset", "object", "embed", "applet", "base",
                         "portal", "input", "button", "textarea", "select", "foreignobject"))
_EXTERNAL = re.compile(r"\s*(?:https?:|//|\\\\)", re.IGNORECASE)
_CSS_EXTERNAL = re.compile(r"url\(\s*['\"]?\s*(?:https?:|//)|@import", re.IGNORECASE)
_STYLE_BLOCK = re.compile(r"<style\b[^>]*>(.*?)</style\s*>", re.IGNORECASE | re.DOTALL)
# リンク（読みに行かない・押した人だけが行く）は外へ向けてよい。
LINK_TAGS = frozenset(("a",))
LINK_ATTRS = frozenset(("href", "xlink:href"))


def _detail(path: str, what: str) -> str:
    return f"{path[:72]}: {what}"


def _text_problem(path: str, data: bytes, kind: str):
    """HTML・SVG・XML の検査。断る (符丁, 詳細) か None。"""
    if len(data) > HTML_MAX:
        return "garden_size_refused", _detail(path, "1 MB")
    if data[:2] in (b"\xfe\xff", b"\xff\xfe") or b"\x00" in data:
        return "garden_text_refused", _detail(path, "utf-8")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return "garden_text_refused", _detail(path, "utf-8")
    if re.search(r"http-equiv", text, re.IGNORECASE):
        return "garden_text_refused", _detail(path, "http-equiv")
    for m in _TAG.finditer(text):
        tag = m.group(1).lower().split(":")[-1]
        if tag in ACTIVE_TAGS:
            return "garden_active_refused", _detail(path, tag)
        for name, raw in _ATTR.findall(m.group(2)):
            name = name.lower()
            value = html.unescape(raw.strip("\"'"))
            if name.startswith("on"):
                return "garden_active_refused", _detail(path, "on-event")
            if re.match(r"\s*(?:javascript|vbscript|data:text/html)", value, re.IGNORECASE):
                return "garden_active_refused", _detail(path, "javascript")
            if name == "style" and _CSS_EXTERNAL.search(value):
                return "garden_external_refused", _detail(path, "style")
            if _EXTERNAL.match(value) and not (tag in LINK_TAGS and name in LINK_ATTRS):
                return "garden_external_refused", _detail(path, f"{tag} {name}"[:20])
            if name in ("srcset", "imagesrcset") and re.search(r"(?:https?:)?//", value, re.IGNORECASE):
                return "garden_external_refused", _detail(path, f"{tag} {name}"[:20])
    for block in _STYLE_BLOCK.findall(text):
        if _CSS_EXTERNAL.search(block):
            return "garden_external_refused", _detail(path, "style")
    if kind == "html" and text.count(GUIDE_MARK) != 1:
        return "garden_guide_missing", path[:80]
    return None


def _css_problem(path: str, data: bytes):
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return "garden_text_refused", _detail(path, "utf-8")
    if _CSS_EXTERNAL.search(text):
        return "garden_external_refused", _detail(path, "css")
    return None


def check_dir(directory: str) -> dict:
    """出す前の検査。通れば `{"files": {"/道": {"sha256", "size"}}, "bytes": 全部}`。外れたら最初の 1 つで断る。"""
    if not isinstance(directory, str) or not os.path.isdir(directory) or os.path.islink(directory):
        raise VaultRefused("garden_dir_missing")
    root = os.path.realpath(directory)
    files, total = {}, 0
    for base, dirs, names in os.walk(root):
        rel_base = os.path.relpath(base, root)
        for name in sorted(dirs):
            rel = name if rel_base == "." else f"{rel_base}/{name}"
            if name.startswith(".") or os.path.islink(os.path.join(base, name)):
                raise VaultRefused("garden_kind_refused", _detail(rel, "dot or link"))
        dirs.sort()
        for name in sorted(names):
            full = os.path.join(base, name)
            rel = (name if rel_base == "." else f"{rel_base}/{name}").replace(os.sep, "/")
            if name.startswith(".") or os.path.islink(full) or not os.path.isfile(full):
                raise VaultRefused("garden_kind_refused", _detail(rel, "dot or link"))
            ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            if ext not in TYPES:
                raise VaultRefused("garden_kind_refused", _detail(rel, "kind"))
            if not PATH.fullmatch(rel) or any(part in (".", "..") for part in rel.split("/")):
                raise VaultRefused("garden_kind_refused", _detail(rel, "name"))
            if len(files) >= FILES_MAX:
                raise VaultRefused("garden_size_refused", f"{FILES_MAX} files")
            size = os.path.getsize(full)
            if size > FILE_MAX:
                raise VaultRefused("garden_size_refused", _detail(rel, "25 MB"))
            with open(full, "rb") as stream:
                data = stream.read()
            problem = _text_problem(rel, data, ext) if ext in TEXT_KINDS else (
                _css_problem(rel, data) if ext == "css" else None)
            if problem:
                raise VaultRefused(*problem)
            total += len(data)
            if total > GARDEN_MAX:
                raise VaultRefused("garden_size_refused", "100 MB")
            files["/" + rel] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    if not files:
        raise VaultRefused("garden_empty")
    return {"files": files, "bytes": total}


# ------------------------------------------------------------------ 庭の URL と置いた目録

def check_url(url) -> str:
    m = VAULT_URL.fullmatch(url) if isinstance(url, str) else None
    if not m or not GARDEN_NAME.fullmatch(m.group(3)) or m.group(3) in GARDEN_RESERVED:
        raise VaultRefused("garden_args_invalid", "url")
    return url.rstrip("/")


def state_path(url: str) -> str:
    m = VAULT_URL.fullmatch(url)
    name = f"{m.group(1)}--{m.group(2)}--{m.group(3)}"
    return os.path.join(accounts.thth_root(), "garden_state", f"{name}.json")


def last_published(url: str) -> dict:
    try:
        with open(state_path(url), encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, ValueError):
        return {}
    files = value.get("files") if isinstance(value, dict) else None
    return files if isinstance(files, dict) else {}


def changes(files: dict, last: dict) -> dict:
    new = sorted(p for p in files if p not in last)
    changed = sorted(p for p in files if p in last and last[p] != files[p]["sha256"])
    removed = sorted(p for p in last if p not in files)
    return {"new": new, "changed": changed, "removed": removed, "first": not last}


def digest(url: str, files: dict) -> str:
    joined = "\x1f".join(["thth_garden_publish/1", url] + [f"{p}={files[p]['sha256']}" for p in sorted(files)])
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12]


MONBAN_TO_GARDEN = {
    "garden_token_missing": "garden_not_ready",
    "garden_missing": "garden_not_ready",
    "garden_token_required": "garden_not_ready",
    "stale_head": "garden_busy",
    "too_many_pending": "garden_busy",
    "rate_limited": "garden_busy",
    "garden_full": "garden_full",
    "invalid_key": "garden_login_required",
    "not_logged_in": "garden_login_required",
    "login_expired": "garden_login_required",
    "remote_unavailable": "garden_unavailable",
    "invalid_vault_url": "garden_args_invalid",
}


def _monban_refusal(rc: int, out: str, err: str, step: str) -> VaultRefused:
    got = wire.monban_json(out)
    code = got.get("error") or wire.monban_code(err)
    if code in MONBAN_TO_GARDEN:
        return VaultRefused(MONBAN_TO_GARDEN[code], code)
    return VaultRefused("garden_failed", f"{step} {code}" if code else step)


def plan(directory: str, url: str) -> dict:
    url = check_url(url)
    found = check_dir(directory)
    return {"url": url, "directory": os.path.realpath(directory), "files": found["files"], "bytes": found["bytes"],
            "changes": changes(found["files"], last_published(url)), "digest": digest(url, found["files"])}


def publish(found: dict) -> dict:
    """二段目: monban-vault garden publish を呼ぶ（`found` は直前に作り直した `plan`）。"""
    exe = tools.need(tools.MONBAN_VAULT, "garden")
    rc, out, err = tools.run([exe, "garden", "publish", found["url"], found["directory"], "--json"])
    if rc != 0:
        raise _monban_refusal(rc, out, err, "publish")
    got = wire.monban_json(out)
    record = {"schema": "thth.garden_published/1", "url": found["url"], "head": got.get("head"),
              "files": {p: v["sha256"] for p, v in found["files"].items()}}
    secrets_fs.atomic_write_json(state_path(found["url"]), record, mode=0o600)
    return {"head": got.get("head"), "files": got.get("files", len(found["files"])),
            "bytes": got.get("bytes", found["bytes"])}


def simple(url: str, verb: str) -> dict:
    """`stop`（確認なし・すぐ）と `status`（読むだけ）。"""
    url = check_url(url)
    exe = tools.need(tools.MONBAN_VAULT, "garden")
    rc, out, err = tools.run([exe, "garden", verb, url, "--json"])
    if rc != 0:
        raise _monban_refusal(rc, out, err, verb)
    got = wire.monban_json(out)
    return {"url": url, **{k: v for k, v in got.items() if k != "error"}}


# ------------------------------------------------------------------ 店の写しから頁を作る

OUTPUT_FILES = ("index.html", "items.html", "style.css")
TITLE_MAX = 60
_SHOP_URL = re.compile(r"https://[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?(?::[0-9]{1,5})?(?:/[A-Za-z0-9._~%/-]*)?\Z")
GUIDE = ('<aside class="thth-guide" ' + GUIDE_MARK + '><p>THTH の案内: この頁は '
         '<a href="https://thth.me/" rel="noopener">THTH</a> の無料版で作りました。</p></aside>')
STYLE = """body{margin:0 auto;max-width:42rem;padding:1.5rem 1rem;font-family:system-ui,sans-serif;line-height:1.8;color:#222}
h1{font-size:1.5rem;line-height:1.4}
ul.items{list-style:none;padding:0}
ul.items li{border-bottom:1px solid #ddd;padding:.75rem 0}
.price{font-variant-numeric:tabular-nums}
.thth-guide{margin-top:2rem;padding:.75rem 1rem;border:1px solid #ccc;border-radius:.5rem;font-size:.875rem;color:#555}
footer{margin-top:1.5rem;font-size:.75rem;color:#777}
"""


def _t(value) -> str:
    """本文に入れる文。escape したうえで、Edge が文の中でも断る `http-equiv` の字面を崩す（見た目は同じ）。"""
    out = html.escape(str(value), quote=True)
    return re.sub(r"(?i)(http)-(equiv)", r"\1&#45;\2", out)


def _page(title: str, body: str) -> str:
    return ("<!DOCTYPE html>\n<html lang=\"ja\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            f"<title>{_t(title)}</title>\n<link rel=\"stylesheet\" href=\"style.css\">\n</head>\n<body>\n"
            f"{body}\n{GUIDE}\n</body>\n</html>\n")


def _price(row: dict) -> str:
    price = row.get("price")
    return f"{price:,} 円" if isinstance(price, int) and not isinstance(price, bool) else "価格は店の頁で"


def render(title: str, items: list, shop_url: str | None, read_at: str | None) -> dict:
    """`{名前: 文}`。items は公開で買える商品だけ（呼ぶ側で選ぶ）。"""
    buy = (f'<p><a href="{_t(shop_url)}" rel="noopener">お店の頁へ（買うのはこちら）</a></p>' if shop_url else "")
    when = f"<footer><p>商品の様子は {_t(read_at)} に読んだ写しから作りました。</p></footer>" if read_at else ""
    index = _page(title, f"<header><h1>{_t(title)}</h1></header>\n<main>\n"
                         f"<p>今買える商品を {len(items)} 点載せています。</p>\n"
                         f'<p><a href="items.html">商品の一覧</a></p>\n{buy}\n</main>\n{when}')
    rows = "\n".join(f'<li><span class="name">{_t(row.get("name") or "")}</span> '
                     f'<span class="price">{_t(_price(row))}</span></li>' for row in items)
    listing = (f'<ul class="items">\n{rows}\n</ul>' if items else "<p>今買える商品はありません。</p>")
    page = _page(f"商品の一覧 - {title}", f"<header><h1>商品の一覧</h1><p><a href=\"index.html\">{_t(title)}</a></p>"
                                       f"</header>\n<main>\n{listing}\n{buy}\n</main>\n{when}")
    return {"index.html": index, "items.html": page, "style.css": STYLE}


def build(store: str, out: str, *, title: str | None = None, shop_url: str | None = None) -> dict:
    """店の写し（`thth shop diff` が書く 1 枚）から頁を作る。写しを読むだけ（店にも写しにも書かない）。"""
    from ..shop import base, ledger
    from ..shop.wire import StoreRefused
    try:
        ledger.check_name(store)
        snapshot = ledger.read_snapshot(store)
    except StoreRefused as exc:
        raise VaultRefused("garden_snapshot_missing" if exc.code == "store_ledger_invalid" else "garden_args_invalid",
                           "store") from None
    if snapshot is None:
        raise VaultRefused("garden_snapshot_missing", store)
    if shop_url is not None and (len(shop_url) > 300 or not _SHOP_URL.fullmatch(shop_url)):
        raise VaultRefused("garden_args_invalid", "shop-url")
    title = base.clean_text(title if title is not None else store, TITLE_MAX)
    if not title:
        raise VaultRefused("garden_args_invalid", "title")
    rows = [row for row in snapshot["items"].values() if isinstance(row, dict) and base.buyable(row)]
    rows.sort(key=lambda row: (str(row.get("name") or ""), str(row.get("id"))))
    if os.path.lexists(out) and (not os.path.isdir(out) or os.path.islink(out)
                                 or set(os.listdir(out)) - set(OUTPUT_FILES)):
        raise VaultRefused("garden_out_not_empty")
    os.makedirs(out, mode=0o755, exist_ok=True)
    read_at = snapshot.get("read_at") if isinstance(snapshot.get("read_at"), str) else None
    for name, text in render(title, rows, shop_url, read_at and base.clean_text(read_at, 40)).items():
        with open(os.path.join(out, name), "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
    found = check_dir(out)  # 作った物も出す前の検査を通す（柵）
    return {"ok": True, "out": os.path.realpath(out), "items": len(rows),
            "not_listed": len(snapshot["items"]) - len(rows), "files": sorted(found["files"]), "bytes": found["bytes"]}
