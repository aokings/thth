"""`thth article diff <account|project> [--article …] [--delivery …] [--json] [--draft]`（設計 4.0 S5 §3.3）。

**読むだけ。** 配信の台帳の「出した版」（`checked_rev`）と repo の HEAD にある記事の版・画像の指紋を比べ
（`hatena_entry.source_status`）、直した配信ごとに本文の差分・画像の内訳・告知の行を出す。媒体の API は
呼ばない。**ファイルは書かない**（`--draft` は更新の原稿の文面を標準出力に出すだけ。保存・commit・承認は
レーンの LLM と人・§3.5）。検出しても勝手に更新しない。
"""
from __future__ import annotations

import datetime
import difflib
import json
import sys

from . import accounts as accounts_mod
from . import approval as approval_mod
from . import jst

SCHEMA = "thth.article_diff/1"
DRAFT_LEAD_MINUTES = 10


class ArticleDiffError(ValueError):
    """問いが受け取れない（対象が無い）。"""


def _categories_text(values) -> str:
    return "[" + ", ".join(values or []) + "]"


def draft_name(record, now) -> str:
    """保存先の案内（文だけ・THTH は書かない）: `docs/sns/queue/<日付>-<記事の名の末尾>-update.md`。"""
    tail = str(record.get("article") or "article").rsplit("/", 1)[-1] or "article"
    return f"docs/sns/queue/{jst.to_jst(now):%Y-%m-%d}-{tail}-update.md"


def draft_fields(record, cfg=None) -> tuple:
    """原稿に書ける題とカテゴリ（監査 低-4）。`(題, カテゴリ, 注記の list)`。

    queue の front matter は 1 行 1 項目で、値は最初の `:` より後ろをそのまま読む（引用符を外さない）。
    だから題は**引用しないで**そのまま書くのが往復できる形（`:`・`#` を含んでもよい）。改行は書けないので空白に。
    カテゴリは `[a, b]` を `,` で分けて読み、両端の引用符を落とすので、`,`・`[`・`]`・両端の引用符・制御文字を
    含むもの（はてなから取り込んだカテゴリにありうる）は書けない——文面から外して注記で言う。
    """
    from . import hatena_entry, queuefile
    notes = []
    if cfg is None:
        try:
            cfg = accounts_mod.load_account(record.get("account"))
        except (accounts_mod.AccountError, ValueError, TypeError):
            cfg = {}
    # 実機の確かめ 10/17 の 13: 台帳に題が無い（S5 より前の配信）なら前に送った原稿から引く。
    sent_title, sent_categories = hatena_entry.sent_title_categories(cfg, record)
    record = dict(record, title=sent_title, categories=sent_categories)
    if not sent_title:
        notes.append("題が台帳にも前に送った原稿にもありません。保存する前に title: に題を書いてください")
    title = record.get("title") if isinstance(record.get("title"), str) else ""
    if "\n" in title or "\r" in title or queuefile.find_control_char(title):
        title = " ".join("".join(ch if ch.isprintable() else " " for ch in title).split())
        notes.append("題に改行か制御文字があったので空白にしました")
    title = title.strip()
    categories = []
    for item in record.get("categories") if isinstance(record.get("categories"), list) else []:
        text = str(item).strip()
        if (not text or any(ch in text for ch in ",[]\x1e\x1f") or text[0] in "'\"" or text[-1] in "'\""
                or queuefile.find_control_char(text) or len(text) > hatena_entry.CATEGORY_CHARS_MAX):
            notes.append(f"カテゴリ「{text}」は原稿の書式で書けません（, や [ ] や引用符を含む）。文面から外しました")
            continue
        categories.append(text)
    return title, categories, notes


def draft_text(record, now, cfg=None) -> str:
    """更新の原稿の文面（§3.5）。欄は台帳から写す（写し間違いを無くす）。`publish_at` は今＋10 分（分まで）。"""
    at = jst.to_jst(now).replace(second=0, microsecond=0) + datetime.timedelta(minutes=DRAFT_LEAD_MINUTES)
    title, categories, _notes = draft_fields(record, cfg)
    lines = ["---", "thth: 1", f"account: {record['account']}", "status: draft", f"publish_at: {jst.iso(at)}",
             f"article: {record.get('article')}", f"article_source: {record.get('article_source')}",
             f"form: {record.get('form') or 'full'}", f"title: {title}",
             f"categories: {_categories_text(categories)}", "op: update", f"delivery: {record['delivery']}",
             "---", "", "## hatenablog", ""]
    return "\n".join(lines)


def _current(record, cfg) -> tuple:
    """今の記事（作業ツリー）を 1 段目と同じ変換に通す。`(本文, 画像, 注記の list, 断りの符丁)`。"""
    from . import hatena_entry
    notes = []
    # 実機の確かめ 10/17 の 12: 承認したときと同じ変換に通す（題が分からないと先頭の `# 題` が外れず、
    # 毎回 `+# 題` の偽の差分が出た）。題は台帳か前に送った原稿から。
    title, _categories = hatena_entry.sent_title_categories(cfg, record)
    try:
        path = hatena_entry.repo_file(cfg.get("repo_dir"), record.get("article_source"))
        data, body, images = hatena_entry._render(cfg, record.get("article_source"), path, title=title or None)
    except hatena_entry.EntryError as exc:
        return None, None, notes, exc.code
    try:
        hatena_entry.committed_commit(path, data)
    except hatena_entry.EntryError as exc:
        notes.append(hatena_entry.message(exc.code))
    return body, images, notes, None


def _embed_like_sent(record, cfg, body):
    """今の本文の画像の参照を、主体の画像の台帳の配置で置き換える（HTTP を打たない）。組めなければ None。"""
    from . import hatena_convert, hatena_fotolife, principals
    try:
        placements = hatena_fotolife.placements_from_ledger(record.get("principal"), record.get("images") or [])
        if not placements:
            return None
        hatena_id = principals.load_principal(record.get("principal")).get("hatena_id")
        return hatena_convert.embed_images(body, placements, article_source=record.get("article_source"),
                                           hatena_id=hatena_id)
    except Exception:  # noqa: BLE001 — 組めなければ今のまま（注記が残る）
        return None


def _one(record, cfg, *, draft, now, allowed_names) -> dict:
    from . import deliveries_cli, hatena_entry
    body, images, notes, error = _current(record, cfg)
    row = {"delivery": record["delivery"], "account": record["account"], "article": record.get("article"),
           "article_source": record.get("article_source"), "state": record["state"],
           "checked_at": record.get("checked_at"), "published_url": record.get("published_url"),
           "source": record["source"], "diff_lines": [], "diff_from": None, "notes": notes,
           "images": None, "announcements": []}
    if error is not None:
        row["notes"].append(hatena_entry.message(error))
    else:
        before, origin, note = hatena_entry.previous_body(record["account"], record, has_images=bool(images))
        row["diff_from"] = origin
        if origin in ("sent", "adopted") and images:
            # 前の本文は画像の参照を置き換えたあとの形。今の本文も同じ形に置き換えて比べる（12）。
            embedded = _embed_like_sent(record, cfg, body)
            if embedded is not None:
                body, note = embedded, None
        if note:
            row["notes"].append(note)
        if before is None:
            row["notes"].append("前の本文の控えが無いので差分は出せません（今の本文の全文）")
            row["diff_lines"] = body.split("\n")
        else:
            row["diff_lines"] = list(difflib.unified_diff(approval_mod.normalize_hatena_body(before).split("\n"),
                                                          body.split("\n"), "出した版", "今の版", lineterm=""))
        row["images"] = hatena_entry.image_changes(record.get("images"), images)
    told, _broken = deliveries_cli.announcements(cfg.get("project"), {record["delivery"]},
                                                 allowed_names=allowed_names)
    row["announcements"] = told
    if draft:
        row["draft_text"] = draft_text(record, now, cfg)
        row["notes"] += draft_fields(record, cfg)[2]
        row["draft_name"] = draft_name(record, now)
    return row


def build(target, *, article=None, delivery=None, draft=False, now=None, allowed_names=None) -> dict:
    from . import deliveries_cli
    now = now if now is not None else jst.now_jst()
    try:
        listing = deliveries_cli.build(target, article=article, now=now, allowed_names=allowed_names)
    except deliveries_cli.DeliveriesError:
        raise ArticleDiffError("target_unknown") from None
    configs = {}
    counts = {"behind": 0, "same": 0, "no_follow": 0, "cannot_say": 0}
    rows, cannot_say = [], set(listing["cannot_say"]) - {"sent_unreadable"}
    for record in listing["deliveries"]:
        if delivery is not None and record["delivery"] != delivery:
            continue
        source = record.get("source")
        if source is None:
            continue
        if "no_follow" in (source.get("reasons") or []):
            counts["no_follow"] += 1
            continue
        if source.get("behind") is None:
            counts["cannot_say"] += 1
            continue
        if not source["behind"]:
            counts["same"] += 1
            continue
        counts["behind"] += 1
        name = record["account"]
        if name not in configs:
            configs[name] = accounts_mod.load_account(name)
        rows.append(_one(record, configs[name], draft=draft, now=now, allowed_names=allowed_names))
    return {"schema": SCHEMA, "report_type": "article_diff", "generated_at": jst.iso(now), "target": target,
            "target_kind": listing["target_kind"], "article": article, "delivery": delivery, "counts": counts,
            "deliveries": rows, "unreadable": listing["unreadable"], "cannot_say": sorted(cannot_say),
            "limitations": ["読むだけ。媒体の API は呼ばず、台帳と repo の HEAD（記事は作業ツリーで変換）を比べる",
                            "検出しても勝手に更新しない。更新は op: update の原稿を保存して thth approve"]}


def _short(at) -> str:
    from . import hatena_entry
    return hatena_entry.short_at(at)


def render(payload, out=print) -> None:
    from . import hatena_entry
    c = payload["counts"]
    out(f"記事の修正  {payload['target']}  直した {c['behind']} 件・同じ {c['same']} 件・"
        f"追従しない {c['no_follow']} 件・言えない {c['cannot_say']} 件")
    for row in payload["deliveries"]:
        s = row["source"]
        commit = f"（commit {s['head_commit'][:12]}…）" if s.get("head_commit") else ""
        img = (f"  画像: {hatena_entry.image_changes_text(row['images'])}" if row["images"] is not None else "")
        out(f"  {row['delivery']}  {row['article']}  {row['state']}  出した {(s.get('checked_rev') or '')[:12]}…"
            f"（{_short(row.get('checked_at'))}）→ 今の版 {(s.get('current_rev') or '')[:12]}…{commit}{img}")
        for note in row["notes"]:
            out(f"  ⚠ {note}")
        out("  ---- 本文の差分（出した版 → 今の版） ----")
        for line in row["diff_lines"]:
            out("  " + line)
        out("  ---- ここまで ----")
        for told in row["announcements"]:
            gone = "（記事は消えています）" if row["state"] == "deleted" else ""
            out(f"  告知: {told['account']} {_short(told.get('sent_at'))}（{told['post_id']}）→ 書き換えません"
                f"（訂正を出すなら返信の原稿で）{gone}")
        if row.get("draft_text") is not None:
            out(f"  ---- 更新の原稿の文面（{row['draft_name']} に保存して thth approve・THTH は書きません） ----")
            for line in row["draft_text"].split("\n"):
                out("  " + line)
            out("  ---- ここまで ----")
        else:
            out(f"  更新するには: 更新の原稿を docs/sns/queue/ に書いて thth approve（thth article diff "
                f"{row['account']} --delivery {row['delivery']} --draft で文面が出ます）")
    if payload["unreadable"]:
        out("読めなかったもの: " + "・".join(
            f"{u['account']}/{u['delivery'] or '*'}（{u['reason']}）" for u in payload["unreadable"]))


def register(sub) -> None:
    p = sub.add_parser("article", help="記事の正本（repo）の修正を検出する・反応を束ねる（読むだけ・4.0 S5・S6）")
    inner = p.add_subparsers(dest="article_action", required=True)
    d = inner.add_parser("diff", help="出した版と repo の HEAD の版を比べ、直した配信の差分を出す（読むだけ）")
    d.add_argument("account", metavar="target", help="account 名か project 名")
    d.add_argument("--article", default=None, help="記事の id で絞る")
    d.add_argument("--delivery", default=None, help="配信 id で絞る")
    d.add_argument("--draft", action="store_true",
                   help="更新の原稿の文面を標準出力に出す（ファイルは書かない）")
    d.add_argument("--json", action="store_true")
    d.set_defaults(func=cmd)
    # 4.0 S6 §5: `thth article reactions`（反応を記事の名で束ねる・読むだけ）。
    from . import article_reactions
    article_reactions.register(inner)


def cmd(args) -> int:
    try:
        payload = build(args.account, article=args.article, delivery=args.delivery, draft=args.draft)
    except (ArticleDiffError, accounts_mod.AccountError) as exc:
        print(str(exc), file=sys.stderr)
        if args.json:
            print(json.dumps({"schema": SCHEMA, "cannot_say": [str(exc)]}, ensure_ascii=False))
        return 2
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    render(payload)
    return 0
