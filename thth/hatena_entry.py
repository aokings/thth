"""はてなブログの配信原稿（queue の 1 本）を読む・検査する・承認の材料にする（設計 4.0 S1 §3.5・§5・§6）。

配信原稿は今の queue と同じ `thth: 1` の front matter で、はてなの口座（`media: hatenablog`）の
ときだけ次の欄が効く: `article`・`article_source`・`form`・`title`・`categories`・`op`・
`delivery`。本文の出所は `article_source` だけ（`## hatenablog` の節は使わない）。

ここに置くのは、lint・`thth approve`（1 段目・2 段目）・遠くの道の予約・timer の照合の
**4 つの入口が同じ材料を作る 1 か所**（`material()`）。承認の指紋は
`approval.compute_hatena_entry_sha()`、本文の変換は `hatena_convert.render()`（S2 の規則の表）。

**blog_id は原稿から取らない**（§5.1）。口座の台帳の `blog_id` を指紋に入れ、原稿に
`blog_id:` があれば lint が `blog_id_in_manuscript` で断る。
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import posixpath

from . import accounts as accounts_mod
from . import approval as approval_mod
from . import deliveries as deliveries_mod
from . import hatena_convert
from . import jst
from . import queuefile

KIND = "hatena_entry"
OPS = approval_mod.HATENA_ENTRY_OPS
FORMS = approval_mod.HATENA_ENTRY_FORMS
OP_LABELS = {"create": "新規", "update": "更新", "delete": "削除"}
CATEGORIES_MAX = 10
CATEGORY_CHARS_MAX = 40
TITLE_CHARS_MAX = 200

# lint の符丁（設計 §3.5 の 8 つ＋この実装で足した 3 つ）。1 行は `<符丁>: <人向けの文>`。
LINT_CODES = ("article_required", "article_source_missing", "invalid_article_source", "title_required",
              "invalid_op", "delivery_required", "blog_id_in_manuscript", "categories_invalid",
              # 足した 3 つ（設計に名前が無かった）: form の値・delivery の指す台帳・画像。
              "invalid_form", "delivery_unknown", "image_missing",
              # 4.0 S2: 相対リンク（§4.2）・記事の commit（§7）。S2 の画像の断り
              # （images_unsupported_until_s3）は S3 で外した。
              "relative_link_unresolvable", "article_not_committed",
              # 4.0 S3 §9: 画像の形式・容量・枚数（THTH の側の上限）。
              "image_too_large", "image_type_unsupported", "too_many_images", "images_too_large",
              # 実機の確かめ 10/17 の 2: `delivery:` の配信の台帳が合わない状態のとき、状態をそのまま言う
              # （`_ledger_for` の `delivery_state_<状態>`。`delivery_unknown`＝台帳に無い、に丸めない）。
              *("delivery_state_" + s for s in deliveries_mod.STATES))
# S2・S3 の断り（lint・承認の 1 段目・timer の 3 か所で同じ符丁）。timer の照合でも `approval_stale` に
# 丸めずにこの符丁のまま返す（人が直す先が違う）。
S3_IMAGE_REFUSALS = ("image_too_large", "image_type_unsupported", "too_many_images", "images_too_large")
S2_REFUSALS = ("relative_link_unresolvable", "article_not_committed") + S3_IMAGE_REFUSALS
_MESSAGES = {
    "article_required": "article: が要ります（記事の名前・例 kopicha/2026-10-arabica）",
    "article_source_missing": "article_source: が無いか、指すファイルが repo にありません",
    "invalid_article_source": "article_source: は repo の中の相対パスだけです（`..`・絶対パス・`.` 始まりは不可）",
    "title_required": "title: が要ります（はてなブログの記事の題）",
    "invalid_op": "op: は create・update・delete のどれかです",
    "delivery_required": "op: update・delete には delivery:（配信 id）が要ります",
    "blog_id_in_manuscript": "blog_id: は原稿に書きません（口座の台帳から引きます）",
    "categories_invalid": f"categories: は {CATEGORIES_MAX} 個まで・1 つ {CATEGORY_CHARS_MAX} 字までです",
    "invalid_form": "form: は full・summary・entry のどれかです",
    "delivery_unknown": "delivery: の配信がこの口座の台帳にありません（create で書けるのは取り消した予約の配信だけ）",
    "image_missing": "本文の画像（![](<パス>)）が repo にありません",
    "image_too_large": "本文の画像が 1 枚の上限（既定 8 MiB・主体の台帳の fotolife.max_image_bytes）を超えています",
    "image_type_unsupported": "本文の画像の形式が JPEG・PNG・GIF ではありません（中身の先頭で判定・拡張子は見ません）",
    "too_many_images": "本文の画像が 1 記事の上限（既定 20 枚・fotolife.max_images_per_entry）を超えています",
    "images_too_large": "本文の画像の合計が上限（既定 64 MiB・fotolife.max_total_bytes）を超えています",
    "relative_link_unresolvable": ("記事に相対リンクがありますが、口座の台帳に link_base（相対リンクの基準の"
                                   " URL・https）がありません。link_base を書くか、リンクを絶対 URL に"),
    "delivery_state_wanted": "delivery: の配信はまだ出していません（wanted）。update・delete は出したあとの配信だけです",
    "delivery_state_inflight": ("delivery: の配信は送っている途中か、送った結果を確かめていません（inflight）。"
                                "thth deliveries で確かめてから"),
    "delivery_state_unknown": ("delivery: の配信は送った結果が分かりません（unknown）。"
                               "はてなの画面と thth deliveries で確かめてから"),
    "delivery_state_published": ("delivery: の配信はもう出ています（published）。create で書けるのは"
                                 "取り消した予約の配信だけです。直すなら op: update"),
    "delivery_state_conflict": "はてなの側で手直しされたので止めています（conflict）。解く道は 4.0 S5",
    "delivery_state_no_follow": "delivery: の配信は THTH が追従しないことにしたものです（no_follow）",
    "delivery_state_deleted": "もう削除した配信です（deleted）",
    "article_not_committed": ("記事ファイルが commit されていません（作業ツリーの記事が HEAD と違う・"
                              "まだ追跡されていない）。記事を commit してから"),
}


class EntryError(ValueError):
    """配信原稿を材料にできない（`code` は静的な符丁）。"""

    def __init__(self, code: str, detail: str | None = None):
        super().__init__(code)
        self.code = code
        self.detail = detail


def is_hatena(account_cfg) -> bool:
    from . import principals
    return principals.uses_principal(account_cfg)


def message(code: str) -> str:
    return f"{code}: {_MESSAGES.get(code, code)}"


# ------------------------------------------------------------------ 欄を読む

def _text(fm, key) -> str:
    value = fm.get(key)
    return value.strip() if isinstance(value, str) else ""


def op_of(fm) -> str:
    return _text(fm, "op") or "create"


def form_of(fm) -> str:
    return _text(fm, "form") or "full"


def parse_categories(raw) -> list:
    """`categories: [コーヒー, 価格]` か `categories: コーヒー, 価格`。外れれば `categories_invalid`。"""
    if raw is None:
        return []
    if not isinstance(raw, str):
        raise EntryError("categories_invalid")
    text = raw.strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    if not text.strip():
        return []
    out = []
    for part in text.split(","):
        item = part.strip().strip("'\"").strip()
        if not item:
            continue
        if len(item) > CATEGORY_CHARS_MAX or queuefile.find_control_char(item) or any(
                ch in item for ch in ("\x1e", "\x1f", "[", "]")):
            raise EntryError("categories_invalid")
        out.append(item)
    if len(out) > CATEGORIES_MAX:
        raise EntryError("categories_invalid")
    return out


def repo_file(repo_dir, rel) -> str:
    """repo の中の相対パスを絶対パスに。外れれば `invalid_article_source`。"""
    if not isinstance(rel, str) or not rel.strip() or not isinstance(repo_dir, str) or not repo_dir:
        raise EntryError("invalid_article_source")
    rel = rel.strip()
    if rel.startswith("/") or "\\" in rel or os.path.isabs(rel):
        raise EntryError("invalid_article_source")
    parts = rel.split("/")
    if any(p in ("", "..") or p.startswith(".") for p in parts):
        raise EntryError("invalid_article_source")
    path = os.path.join(repo_dir, *parts)
    real_repo = os.path.realpath(repo_dir)
    if os.path.commonpath([real_repo, os.path.realpath(path)]) != real_repo:
        raise EntryError("invalid_article_source")
    return path


def article_rev(path: str, data: bytes) -> tuple:
    """`(kind, rev)`。repo の中なら記事ファイルの blob の sha（`git hash-object` と同じ値）、
    外なら本文の sha256（設計 §3.4・HEAD の commit にしない）。"""
    from . import writeback
    if writeback.repo_toplevel(path):
        return "git", hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
    return "content", hashlib.sha256(data).hexdigest()


# ------------------------------------------------------------------ lint

def lint_errors(fm, account_cfg) -> list:
    """配信原稿の lint（`<符丁>: <文>` の行）。台帳・記事・配信の台帳は読むが、何も書かない。"""
    out = []
    if "blog_id" in fm:
        out.append(message("blog_id_in_manuscript"))
    op = op_of(fm)
    if op not in OPS:
        out.append(message("invalid_op"))
    if not _text(fm, "article"):
        out.append(message("article_required"))
    if form_of(fm) not in FORMS:
        out.append(message("invalid_form"))
    if op in ("create", "update") and not _text(fm, "title"):
        out.append(message("title_required"))
    try:
        parse_categories(fm.get("categories"))
    except EntryError as exc:
        out.append(message(exc.code))
    if op in ("create", "update"):
        source = _text(fm, "article_source")
        if not source:
            out.append(message("article_source_missing"))
        else:
            try:
                path = repo_file(account_cfg.get("repo_dir"), source)
            except EntryError as exc:
                out.append(message(exc.code))
            else:
                if not os.path.isfile(path):
                    out.append(message("article_source_missing"))
                else:
                    try:
                        data, _body, _images = _render(account_cfg, source, path, title=_text(fm, "title"))
                    except EntryError as exc:
                        out.append(message(exc.code if exc.code in LINT_CODES else "image_missing"))
                    else:
                        try:
                            committed_commit(path, data)
                        except EntryError as exc:
                            out.append(message(exc.code))
    delivery = _text(fm, "delivery")
    if op in ("update", "delete") and not delivery:
        out.append(message("delivery_required"))
    if delivery:
        try:
            _ledger_for(fm.get("account"), op, delivery)
        except EntryError as exc:
            out.append(message(exc.code if exc.code in LINT_CODES else "delivery_unknown"))
    return out


def _ledger_for(account, op, delivery):
    """原稿の `delivery:` が指す台帳（create は `wanted`・update／delete は `published` のものだけ）。"""
    if not deliveries_mod.is_delivery_id(delivery) or not accounts_mod.name_is_safe(account):
        raise EntryError("delivery_unknown")
    try:
        record = deliveries_mod.read(account, delivery)
    except deliveries_mod.DeliveryError:
        raise EntryError("delivery_unknown") from None
    want = "wanted" if op == "create" else "published"
    if record["state"] != want:
        raise EntryError("delivery_state_" + record["state"])
    return record


# ------------------------------------------------------------------ 材料

def committed_rev(path: str) -> str | None:
    """HEAD にある記事ファイルの blob の sha（無い・repo でない・読めなければ None）。"""
    from . import writeback
    top = writeback.repo_toplevel(path)
    if not top:
        return None
    rel = posixpath.join(*os.path.relpath(os.path.realpath(path), os.path.realpath(top)).split(os.sep))
    try:
        r = writeback._run_git(top, ["rev-parse", "--verify", "--quiet", f"HEAD:{rel}"])
    except (OSError, ValueError):
        return None
    out = (r.stdout or "").strip()
    return out if r.returncode == 0 and out else None


def committed_commit(path: str, data: bytes) -> str | None:
    """記事ファイルが HEAD の blob と同じか確かめ、**その記事ファイルを最後に変えた commit** を返す
    （設計 4.0 S2 §7）。HEAD の commit にしないのは、承認の書き戻しなど記事と関係の無い commit で
    値が動かないように（`article_rev` を blob の sha にしたのと同じ考え・S2 の継ぎ目の照合 10/16）。

    repo の外（`article_rev_kind == content`）は確かめず None。作業ツリーの中身が HEAD の blob と
    違う（未 commit の変更）・HEAD に無い（untracked）・読めない → `article_not_committed`。
    """
    from . import writeback
    kind, rev = article_rev(path, data)
    if kind != "git":
        return None
    if committed_rev(path) != rev:
        raise EntryError("article_not_committed")
    top = writeback.repo_toplevel(path)
    rel = posixpath.join(*os.path.relpath(os.path.realpath(path), os.path.realpath(top)).split(os.sep))
    try:
        r = writeback._run_git(top, ["log", "-1", "--format=%H", "HEAD", "--", rel])
    except (OSError, ValueError):
        raise EntryError("article_not_committed") from None
    head = (r.stdout or "").strip()
    if r.returncode != 0 or not head:
        raise EntryError("article_not_committed")
    return head


def _render(account_cfg, source, path, *, title=None) -> tuple:
    repo = account_cfg.get("repo_dir")
    try:
        with open(path, "rb") as f:
            data = f.read()
        text = data.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        raise EntryError("article_source_missing") from None

    kinds = {}
    seen = {"n": 0, "total": 0, "limits": None}

    def image_sha(rel):
        # 枚数・大きさ・形式は sha を取る前に見る（大きなファイルを丸ごと読まない・監査 低-1）。
        if seen["limits"] is None:
            seen["limits"] = _image_limits(account_cfg)
        lim = seen["limits"]
        seen["n"] += 1
        if seen["n"] > lim["max_images_per_entry"]:
            raise EntryError("too_many_images")
        try:
            full = repo_file(repo, rel)
            size = os.stat(full).st_size
        except (OSError, EntryError):
            raise EntryError("image_missing", rel) from None
        if size <= 0:
            raise EntryError("image_missing", rel)
        if size > lim["max_image_bytes"]:
            raise EntryError("image_too_large", rel)
        seen["total"] += size
        if seen["total"] > lim["max_total_bytes"]:
            raise EntryError("images_too_large")
        try:
            with open(full, "rb") as f:
                kinds[rel] = hatena_convert.image_content_type(f.read(16))
            if kinds[rel] is None:
                raise EntryError("image_type_unsupported", rel)
            return hatena_convert.file_sha256(full)
        except OSError:
            raise EntryError("image_missing", rel) from None

    try:
        body, images = hatena_convert.render(text, article_source=source, image_sha=image_sha,
                                             base_url=accounts_mod.link_base(account_cfg), title=title)
    except hatena_convert.ConvertError as exc:
        code = str(exc)
        raise EntryError(code if code in ("image_missing", "images_unsupported_until_s3",
                                          "relative_link_unresolvable")
                         else "invalid_article_source") from None
    _check_images(account_cfg, images, kinds)
    return data, body, images


def _image_limits(account_cfg) -> dict:
    """主体の台帳の `fotolife` の上限（読めなければ既定。主体の突き合わせは別の段が断る）。"""
    from . import hatena_fotolife, principals
    try:
        principal_cfg = principals.load_principal(account_cfg.get("principal"))
    except (accounts_mod.AccountError, TypeError, ValueError):
        principal_cfg = None
    return hatena_fotolife.limits(principal_cfg)


def _check_images(account_cfg, images, kinds) -> None:
    """形式と容量（設計 S3 §9）。lint・承認の 1 段目・timer の照合が同じ `_render` を通る。"""
    if not images:
        return
    lim = _image_limits(account_cfg)
    for row in images:
        if row["bytes"] <= 0:
            raise EntryError("image_missing", row["path"])
        if kinds.get(row["path"]) is None:
            raise EntryError("image_type_unsupported", row["path"])
        if row["bytes"] > lim["max_image_bytes"]:
            raise EntryError("image_too_large", row["path"])
    if len(images) > lim["max_images_per_entry"]:
        raise EntryError("too_many_images")
    if sum(row["bytes"] for row in images) > lim["max_total_bytes"]:
        raise EntryError("images_too_large")


def material(fm, account_cfg, account_name: str, *, ledger: dict | None = None) -> dict:
    """承認の指紋の材料と、見せるための値（何も書かない）。外れれば `EntryError`。

    - blog_id は**口座の台帳から**（原稿には書かせない）。
    - update／delete は配信の台帳から `entry_id`・`readback_sha`（変更前の版）を引く。
    - 本文は `article_source` を `hatena_convert.render()` に通したもの（delete は空）。

    `ledger` を渡すと配信の台帳を読まずにそれを使う（inflight を人が解くとき、送る前の台帳で
    指紋を組み立て直すため・`hatena_publish.resolve`）。
    """
    given = ledger
    op = op_of(fm)
    if op not in OPS:
        raise EntryError("invalid_op")
    form = form_of(fm)
    if form not in FORMS:
        raise EntryError("invalid_form")
    if "blog_id" in fm:
        raise EntryError("blog_id_in_manuscript")
    article = _text(fm, "article")
    if not article:
        raise EntryError("article_required")
    title = _text(fm, "title")
    if op != "delete" and not title:
        raise EntryError("title_required")
    categories = parse_categories(fm.get("categories"))
    delivery = _text(fm, "delivery") or None
    ledger = None
    if op != "create" and not delivery:
        raise EntryError("delivery_required")
    if delivery and given is not None:
        if given.get("delivery") != delivery or given.get("account") != account_name:
            raise EntryError("delivery_unknown")
        ledger = given
    elif delivery:
        ledger = _ledger_for(account_name, op, delivery)
    if ledger is not None and (ledger.get("blog_id") != str(account_cfg.get("blog_id") or "").strip().lower()
                               or ledger.get("principal") != account_cfg.get("principal")):
        # 台帳を作ったあとに口座の blog_id・主体を付け替えた（配信の行き先は後から変えない）。
        raise EntryError("delivery_unknown")
    source = _text(fm, "article_source") or None
    data, body, images, rev_kind, rev, commit = b"", "", [], None, None, None
    if op != "delete":
        if not source:
            raise EntryError("article_source_missing")
        path = repo_file(account_cfg.get("repo_dir"), source)
        data, body, images = _render(account_cfg, source, path, title=title)
        rev_kind, rev = article_rev(path, data)
        commit = committed_commit(path, data)
    entry_id = ledger.get("entry_id") if (ledger and op != "create") else None
    base = (ledger.get("readback_sha") or "") if (ledger and op != "create") else ""
    if op != "create" and not entry_id:
        raise EntryError("delivery_unknown")
    blog_id = str(account_cfg.get("blog_id") or "").strip().lower()
    kwargs = dict(account=account_name, blog_id=blog_id, op=op, entry_id=entry_id,
                  base_readback_sha=base, title=title, categories=categories, body=body,
                  images=[{"path": r["path"], "source_sha256": r["source_sha256"]} for r in images],
                  article=article, form=form, publish_at=fm.get("publish_at"))
    try:
        sha = approval_mod.compute_hatena_entry_sha(**kwargs)
    except ValueError as exc:
        raise EntryError(str(exc)) from None
    return {"op": op, "form": form, "article": article, "article_source": source, "title": title,
            "categories": categories, "delivery": delivery, "ledger": ledger, "body": body,
            "body_sha256": approval_mod.hatena_body_sha256(body), "images": images,
            "article_rev_kind": rev_kind, "article_rev": rev, "article_bytes": len(data),
            "article_commit": commit,
            "entry_id": entry_id, "base_readback_sha": base, "blog_id": blog_id,
            "principal": account_cfg.get("principal"), "project": account_cfg.get("project"),
            "account": account_name, "publish_at": fm.get("publish_at"), "approved_sha": sha}


# ------------------------------------------------------------------ 承認の 1 段目

def _approved_body(account_name, ledger) -> str | None:
    """この配信で最後に出した版の承認の控え（`deliveries/<delivery>.<指紋 12 桁>.body.md`・元の形）。

    指紋は台帳の最後の `published` の行の `approved_sha`（設計 S3 §5.3）。無ければ None。
    """
    if not isinstance(ledger, dict):
        return None
    sha = next((row.get("approved_sha") for row in reversed(ledger.get("history") or [])
                if row.get("state") == "published" and row.get("approved_sha")), None)
    delivery = ledger.get("delivery")
    if not isinstance(sha, str) or len(sha) < 12 or not deliveries_mod.is_delivery_id(delivery):
        return None
    try:
        with open(os.path.join(deliveries_mod.dir_for(account_name), f"{delivery}.{sha[:12]}.body.md"),
                  encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError):
        return None
    return text[:-1] if text.endswith("\n") else text


def _previous_text(account_name, delivery) -> str | None:
    """この配信で最後に送った本文（`sent/` の記録から・update の差分の元の倒し先）。

    S3 からは送った本文は画像の参照を置き換えたあとの形なので、まず承認の控え（元の形）を
    `_approved_body` で読み、無いときだけここに倒す（設計 S3 §5.3）。
    """
    from . import sent as sent_mod
    best_at, best = None, None
    for row in sent_mod.records(accounts_mod.state_dir_for(account_name)):
        if row.get("delivery") != delivery or row.get("kind") not in ("entry_create", "entry_update"):
            continue
        at = jst.parse(row.get("sent_at"))
        if at is not None and (best_at is None or at > best_at):
            best_at, best = at, row.get("text")
    return best if isinstance(best, str) else None


def image_status_text(principal, sha256) -> str:
    """1 段目の画像の行に添える台帳の状態（主体の画像の台帳を読むだけ・HTTP は打たない・S3 §12.1）。"""
    from . import hatena_fotolife
    if not isinstance(principal, str) or not accounts_mod.name_is_safe(principal):
        return "未（公開の直前に上げる）"
    try:
        state, record = hatena_fotolife.status_of(principal, sha256)
    except (OSError, ValueError, accounts_mod.AccountError):
        state, record = "unreadable", None
    if state == "uploaded":
        day = (record.get("uploaded_at") or "")[:10] or "日付不明"
        return f"上がっている（{record.get('syntax')}・{day} に上げた）"
    if state == "unknown":
        return "結果不明（次の run が照合する）"
    if state == "unreadable":
        return "台帳が読めない（thth fotolife で確かめる）"
    return "未（公開の直前に上げる）"


def display_lines(m: dict, *, path: str, digest: str) -> list:
    """承認の 1 段目の表示（設計 §6.3）。本文は全文（update は差分・delete は題と URL）。"""
    head = (f"配信原稿  {m['account']} → {m['blog_id']}（主体 {m['principal']}・レーン {m['project']}）")
    when = f"{m['publish_at']}（timer が draft=no で送る）"
    lines = [head, f"操作: {OP_LABELS[m['op']]}   公開: {when}"]
    if m["op"] != "delete":
        rev = (m["article_rev"] or "")[:12]
        commit = f"・commit {m['article_commit'][:12]}…" if m.get("article_commit") else ""
        lines.append(f"記事: {m['article']}（{m['article_source']}・版 {rev}… {m['article_rev_kind']}）{commit}")
        cats = "・".join(m["categories"]) if m["categories"] else "（なし）"
        lines.append(f"題: {m['title']}   カテゴリ: {cats}")
        if m["images"]:
            lines.append(f"画像 {len(m['images'])} 件:")
            for row in m["images"]:
                lines.append(f"  {row['path']}（{row['bytes']:,} bytes・sha256 {row['source_sha256'][:12]}…）"
                             f"  {image_status_text(m.get('principal'), row['source_sha256'])}")
        else:
            lines.append("画像: なし")
    ledger = m.get("ledger") or {}
    if m["op"] == "update":
        lines.append(f"変更前の版 readback_sha {(m['base_readback_sha'] or '（なし）')[:12]}…"
                     f"（{ledger.get('checked_at') or '時刻不明'} に確かめた）")
        before = _approved_body(m["account"], ledger)
        note = None
        if before is None:
            before = _previous_text(m["account"], m["delivery"])
            if before is not None and m["images"]:
                note = "（承認の控えが無いので送った本文と比べます。画像の行は置き換えのため差が出ます）"
        if before is None:
            lines.append("---- 変換したあとの本文（全文・前の本文の控えが無いので差分は出せません） ----")
            lines += m["body"].split("\n")
        else:
            lines.append("---- 本文の差分（前に送った本文 → 今回） ----")
            if note:
                lines.append(note)
            lines += list(difflib.unified_diff(approval_mod.normalize_hatena_body(before).split("\n"),
                                               m["body"].split("\n"), "前", "今回", lineterm=""))
        lines.append("---- ここまで ----")
    elif m["op"] == "delete":
        lines.append(f"題: {m['title'] or '（原稿に無し）'}")
        lines.append(f"公開 URL: {ledger.get('published_url') or '（台帳に無し）'}")
        lines.append(f"entry_id: {m['entry_id']}")
    else:
        lines.append("---- 変換したあとの本文（全文） ----")
        lines += m["body"].split("\n")
        lines.append("---- ここまで ----")
    lines.append(f"digest: {digest}")
    lines.append(f"承認する: thth approve {path} --confirm {digest} --by <名前>")
    return lines


def prepare(path: str, qf, account_cfg, account_name: str, *, warning=None):
    """`thth approve` の準備（`cli._prepare_one` のはてなの枝）。`(準備, 断る理由)`。"""
    from . import goals as goals_mod
    fm = qf.front_matter
    try:
        m = material(fm, account_cfg, account_name)
    except EntryError as exc:
        return None, f"{path}: {message(exc.code)}"
    control = queuefile.find_control_char(m["body"]) or queuefile.find_control_char(m["title"])
    if control is not None:
        return None, f"{path}: {queuefile.control_char_message(*control)}"
    digest = m["approved_sha"][:approval_mod.APPROVE_DIGEST_LENGTH]
    one = {
        "path": path, "kind": KIND, "warning": warning, "account": account_name,
        "publish_at": fm.get("publish_at"), "topic": None, "reply_to": None, "reply_to_file": None,
        "goal": goals_mod.goal_of(qf), "text": m["body"], "approved_sha": m["approved_sha"],
        "digest": digest, "delivery": m["delivery"],
        "hatena": {key: m[key] for key in ("op", "form", "article", "article_source", "title", "categories",
                                           "blog_id", "principal", "project", "entry_id",
                                           "base_readback_sha", "article_rev", "article_rev_kind",
                                           "body_sha256")}
        | {"images": [dict(row) for row in m["images"]]},
        "_material": m,
    }
    one["hatena"]["delivery"] = m["delivery"]
    one["display_lines"] = display_lines(m, path=path, digest=digest)
    return one, None


def _pending_dir(account_name: str, approved_sha: str) -> str:
    return os.path.join(accounts_mod.state_dir_for(account_name), "approve_pending", "hatena", approved_sha[:24])


def record_first_stage(one: dict) -> bool:
    """1 段目で見せた本文と画像の一覧を `state/<account>/approve_pending/hatena/<指紋>/` に固定する。

    設計 §3.5 は `approve_pending/<delivery>/` と書くが、delivery は 2 段目で振るので
    1 段目には無い。指紋の先頭 24 桁で置く（同じ中身なら同じ置き場）。書けなくても止めない。
    """
    m = one.get("_material") or {}
    try:
        d = _pending_dir(one["account"], one["approved_sha"])
        os.makedirs(d, mode=0o700, exist_ok=True)
        _write_private(os.path.join(d, "body.md"), (m.get("body") or "") + "\n")
        _write_private(os.path.join(d, "images.json"),
                       json.dumps(m.get("images") or [], ensure_ascii=False, indent=2) + "\n")
        return True
    except (OSError, KeyError, ValueError):
        return False


def _write_private(path: str, text: str) -> None:
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


# ------------------------------------------------------------------ 承認の 2 段目

def assign_delivery(one: dict, *, by: str, at: str, origin: dict | None = None) -> str:
    """2 段目: 配信の台帳を作る（create は `wanted`）。振った delivery を `one["delivery"]` に置いて返す。

    - create で原稿に delivery が無い: 新しい台帳を `seq: 1`・`wanted` で作る。
    - create で取り消した予約の delivery がある（台帳が `wanted`）: 同じ台帳に `wanted` の行を足す
      （承認し直した中身を記録する。消さない・振り直さない）。
    - update／delete: 台帳は `published` のまま（状態は公開の側でだけ変わる）。

    承認した本文の控えは `deliveries/<delivery>.<指紋 12 桁>.body.md` に残し、1 段目の控えは消す。
    """
    m = one["_material"]
    account = one["account"]
    rel_queue = _queue_rel(one["path"], m)
    row = {"op": m["op"], "approved_sha": one["approved_sha"], "approved_by": by,
           "origin": dict(origin) if origin else None}
    fields = {"article_source": m["article_source"], "form": m["form"], "queue_file": rel_queue}
    if m["op"] != "delete":
        if m.get("article_commit"):
            fields["wanted_commit"] = m["article_commit"]
        fields.update({"wanted_rev": m["article_rev"], "article_rev_kind": m["article_rev_kind"],
                       "images": [{"path": r["path"], "source_sha256": r["source_sha256"]} for r in m["images"]]})
    if m["op"] == "create" and not m["delivery"]:
        record = deliveries_mod.create(account, principal=m["principal"], blog_id=m["blog_id"],
                                       article=m["article"], at=at, state="wanted", fields=fields, row=row)
        delivery = record["delivery"]
    elif m["op"] == "create":
        delivery = m["delivery"]
        deliveries_mod.advance(account, delivery, state="wanted", at=at, fields=fields, row=row)
    else:
        delivery = m["delivery"]
    one["delivery"] = delivery
    _keep_approved_body(account, delivery, one["approved_sha"], m["body"])
    try:
        import shutil
        shutil.rmtree(_pending_dir(account, one["approved_sha"]), ignore_errors=True)
    except OSError:
        pass
    return delivery


def _queue_rel(path, m) -> str | None:
    from . import writeback
    top = writeback.repo_toplevel(path)
    if not top:
        return None
    return posixpath.join(*os.path.relpath(os.path.realpath(path), os.path.realpath(top)).split(os.sep))


def _keep_approved_body(account, delivery, approved_sha, body) -> None:
    try:
        d = deliveries_mod.dir_for(account)
        os.makedirs(d, mode=0o700, exist_ok=True)
        _write_private(os.path.join(d, f"{delivery}.{approved_sha[:12]}.body.md"), body + "\n")
    except OSError:
        pass  # 控えは見せるためのもの。書けなくても承認は変えない（台帳の approved_sha が正本）


# ------------------------------------------------------------------ timer の照合（select）

def select_problem(qf, account_cfg, account_name):
    """timer が出してよいか（`select_one` のはてなの枝・§6.5 の照合）。`(断る理由 | None, 材料)`。

    - `approved_kind: hatena_entry` の原稿だけ（無ければ・違えば `approval_stale`）。
    - `delivery:` が要る（2 段目で振る。無ければ `approval_stale`）。
    - 記事から変換し直して指紋を計算し直し、`approved_sha` と違えば `approval_stale`。
    - 配信の台帳の状態が合わなければ `delivery_state_<状態>`（conflict・unknown・inflight は人が見る）。
    """
    fm = qf.front_matter
    if fm.get("approved_kind") != KIND or not deliveries_mod.is_delivery_id(_text(fm, "delivery")):
        return "approval_stale", None
    try:
        m = material(fm, account_cfg, account_name)
    except EntryError as exc:
        if exc.code.startswith("delivery_state_") or exc.code in S2_REFUSALS:
            return exc.code, None
        return "approval_stale", None
    if not fm.get("approved_sha") or fm.get("approved_sha") != m["approved_sha"]:
        return "approval_stale", m
    control = queuefile.find_control_char(m["body"]) or queuefile.find_control_char(m["title"])
    if control is not None:
        pos, cp = control
        return f"control_char(位置{pos}・U+{cp:04X})", m
    return None, m


# ------------------------------------------------------------------ timer の断りの文・出たあとの欄（S2）

def queue_command_path(path: str) -> str:
    """`thth approve` に渡す原稿のパス（repo の根からの相対・取れなければ渡されたまま）。"""
    from . import writeback
    try:
        top = writeback.repo_toplevel(path)
        if top:
            return posixpath.join(*os.path.relpath(os.path.realpath(path), os.path.realpath(top)).split(os.sep))
    except (OSError, ValueError):
        pass
    return path


def refusal_log_line(path: str, reason: str) -> str:
    """timer が照合で断ったときの run のログの 1 行（設計 4.0 S2 §8 の 2）。次の一手を添える。

    `core._throw_locked`（select の断り）が出す。`hatena_publish.throw_chosen` の 0 段目
    （ロックの中の照合し直し）も同じ文にする（継ぎ目・S2 の adapter 側で差し替える）。
    """
    name = os.path.basename(path)
    if reason == "approval_stale":
        return (f"承認が古いので出しません: {name}（approval_stale）。記事か原稿を直した場合は "
                f"thth approve {queue_command_path(path)} --by <名前> で承認し直してください")
    if reason in S2_REFUSALS:
        return f"出しません: {name}（{message(reason)}）"
    return f"承認が古いので出しません: {name}（{reason}）"


def outcome_fields(m: dict) -> dict:
    """出た（消えた）ときに配信の台帳へ足す欄（設計 4.0 S2 §7）。`checked_commit` は出したときの HEAD。

    `hatena_publish._record_outcome` の `fields`（`checked_rev` の隣）に足す（継ぎ目）。repo の外の
    記事・delete は空。
    """
    return {"checked_commit": m["article_commit"]} if m.get("article_commit") else {}
