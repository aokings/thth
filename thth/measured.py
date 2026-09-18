"""実測を台帳から機械的に並べる（T5・運用指摘 2026-09-12）。

**なぜ要るか。** 運用の担当が、VM の台帳（ndjson）を目で追って実測表を
作っていた。その結果、一晩で 2 回、読み違いが起きた。

  1. 返信の台帳の行（`marks: [1, 6]` が同居）を、views の行と取り違えた
  2. `お茶` の 6 時間値（views 42）が既に入っていたのを見落とし、
     「未取得」と報告した

**現物を目で追うのも十分に間違えます。** 機械的に並べる口が要る。

`thth/collect.py` が書いた `data/sns/insights/posts/*.ndjson`（投稿ごと）と
`data/sns/insights/account/*.ndjson`（アカウント日次）を読む。**読むだけ。
何も書かない。**

**所有 account を根拠付きで選別する**（外部レビュー再判定 M3・2026-09-12、
さらに R3・2026-09-12 で根拠を採取時点のものに絞った）。`repo_dir` は
account ごとに分かれている前提だったが、共有された場合に選別が無く、他
account の投稿・日次が混ざっていた。根拠は 2 つあり、記録され方が違うので
選び方も分ける:

  - 投稿ごとの台帳（`posts/*.ndjson`）の行は、`thth/collect.py` が採取時点に
    `account` を書く（R3 以降）。**行そのものの `account` だけを所有の根拠に
    する。** 行に `account` が無ければ「不明」（`posts_unknown_ownership`）
    ——他 account と判った場合と違い、**この account かもしれないので、値を
    混ぜずに区別して出す**。

    以前（M3）は行に `account` が無いことを前提に、`file`（採取当時の queue
    ファイル名）を手がかりに queue_dir の**現在の**原稿を開き、その
    front-matter の `account` を過去の所有として使っていた。だが原稿の
    account は「いま」の値であって「採取時点」の値ではない——原稿の account を
    書き換えると、過去の台帳が黙って新しい account の実測へ移し替えられて
    しまっていた（R3）。**現在の原稿を過去の所有の根拠にするのをやめる。**
    R3 より前に採取した行（`account` を持たない）は、完全な過去復元を諦めて
    「不明」に分ける。

    **選別は行ごとに行う。** R3 の実装は 1 ファイルの所有を**先頭行 1 行**で
    決めていた。先頭行が R3 より前だと、後から `account` 付きの行がいくら
    足されても、その投稿は永久に「不明」のままになる——実際、kopicha の
    6 投稿すべてがその状態で、道具が空で出続けていた。行ごとに見れば、
    裏付けのある行は裏付けのあるまま出せる。外した行の数は
    `rows_unattributed` として出す（**時系列の頭が欠けているのに、そこから
    始まったかのように読ませない**）。1 つのファイルに 2 つの account の行が
    同居していたら、そのファイルは信用できないので**壊れとして 1 行も使わない**。
  - アカウント日次（`account/*.ndjson`）は `thth/collect.py` の
    `_collect_account_daily()` がファイル名そのものに `<account>-<年月>` を
    刻んでいる。ファイル名が一致しないものは他 account と判っているので、
    ただ除く（「不明」にはしない）。

**採取時点に帰属する値と、現在の原稿から引いた値を区別する**（M4）。`form`
は保存時ではなく「いま queue ファイルにある値」を読んでいるだけなので、
`form`（過去の型であるかのような名前）を出すのをやめ、`form_now` と
`form_source: "current_draft"` で「いまの原稿から引いた値」だと明示する。
採取時点の型は台帳に元から無いので、無いものは無いと出す（推定で埋めない）。
"""
from __future__ import annotations

import json
import os
import re

from . import accounts as accounts_mod
from . import postid as postid_mod
from . import queuefile as queuefile_mod

# **期待する指標名の一覧**（運用指摘 2026-09-12）。台帳の行を通して 1 度も
# 現れない名前を出す。`clicks` は実装の穴で一度も記録されていなかった
# （2026-09-12 に修正済み）——この一覧はその再発を見つけるためのもの。
# **0 と混ぜない**のが要点（規約 12: 判らないものを判らないと言う）。
#
# **層ごとに分ける**（運用指摘 2026-09-12・2 度目）。最初は 1 つの一覧に
# まとめ、投稿単位とアカウント日次の**両方の行を 1 つの集合に混ぜて**突き
# 合わせていた。**が、そもそも取れる指標が層ごとに違う。**
#
#   - 投稿単位（`thth/adapters/threads.py:237`）は `shares` を持つが
#     `clicks`・`followers_count` を**持てない**
#   - アカウント日次（同 `:255`）は `clicks`・`followers_count` を持つが
#     `shares` を**持てない**
#
# 混ぜた集合で判定すると、**片方の層でその指標が 1 度も採れていなくても、
# もう片方の層に出ていれば「欠けていない」になる。** 運用が見つけたのは
# 「`clicks` が無いのに『欠けている指標: 無し』と出る」形だが、**逆向き
# （投稿の `views` が 1 件も無いのに、日次に `views` があるので隠れる）
# のほうが重い。** 層をまたいで数えない。
#
# **キー名も変えた**（`missing_metrics` → 層ごとの 2 つ）。名前を残すと、
# 古い読み手が**黙って**通ってしまう。壊れて気づくほうがよい（規約 5）。
POST_METRIC_NAMES = ("views", "likes", "replies", "reposts", "quotes", "shares")
ACCOUNT_DAILY_METRIC_NAMES = (
    "views", "likes", "replies", "reposts", "quotes", "followers_count", "clicks",
)


def _row_missing(metrics, expected) -> list:
    """**その 1 行に無い指標の名前**（運用指摘 2026-09-12・3 度目）。

    台帳全体の「1 度も現れていない」だけでは足りない。**採取の版が上がると、
    翌日から入った指標が、前日の欠測を隠す。**

    実例: `clicks` を採れるようにした修正が VM に降りたのが 00:08:24。
    その日の日次採取は asmon 00:03:12・nigamilab 00:05:14・kopicha 00:08:32
    ——**kopicha だけが 8 秒差で間に合った。** 日次は日付ごとに 1 度しか採らない
    （`thth/collect.py:332`）ので、**asmon と nigamilab の 2026-09-11 の
    `clicks` は永久に欠測**。翌日ぶんには入るので、**全体の判定では隠れる。**

    行に欄が無いだけなので値 `0` と混ざる心配は薄いが、**「欄が無い＝採る前の
    版だった」と読める手段**が要る。それがこれ。
    """
    have = set((metrics or {}).keys())
    return [m for m in expected if m not in have]


def _read_ndjson(path: str) -> tuple[list, bool]:
    """1 本の ndjson を読む。`(行の配列, 壊れているか)`。

    `thth/replies.py` の `_read_replies_file()` と同じ流儀（この repo の規約）:
    **壊れと不存在を混ぜない**。ファイルが無ければ「まだ採っていないだけ」——
    `broken=False` で空を返す。JSON として読めない行が 1 行でもあれば、その
    ファイルは信用できないので `broken=True` にして**中身は 1 行も使わない**。
    壊れた行の手前までをこっそり混ぜると、「壊れて一部しか読めなかった」を
    「n 件だけだった」と取り違える。
    """
    if not os.path.exists(path):
        return [], False
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return [], True
    rows = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            return [], True
        if not isinstance(row, dict):
            return [], True
        rows.append(row)
    return rows, False


def _form_for(queue_dir: str, file_name: str | None) -> str | None:
    """queue ファイルの front-matter から `form` を引く。**読むだけ**——検証しない。

    投稿の選定に使う口（`core.list_queue_files()`）ではないので、同期を確認した
    commit の中身と一致するかは問わない。連投の段（`thth/collect.py` の
    `_with_bundle_posts()`）は `file` に `"<元のファイル名>#<段番号>"` を残すので、
    `#` の手前で切って実物のファイルを探す。読めなければ（無い・壊れている）
    `None`（取れなかった、であって「型無し」ではないが、この口は区別しない——
    どちらも「判らない」なので `null` でよい）。
    """
    return _form_state(queue_dir, file_name)[0]


def _form_state(queue_dir: str, file_name: str | None) -> tuple:
    """`(型, 読めたか)` を返す（外部レビュー C2・P2・2026-09-12）。

    **`_form_for()` は「読めなかった」も「型が無い」も `None` で返す**——
    docstring にも「取れなかった、であって型無しではない」と書いてあった。
    それなのに `comparability()` がその `None` を
    **「型（`form`）が無い」＝採用不可**に変換していた。**自分で書いた但し書きを、
    自分で踏んだ。**

    **読めたときだけ「型が無い」と言える。**
    """
    if not file_name:
        return (None, False)
    base = file_name.split("#", 1)[0]
    path = os.path.join(queue_dir, base)
    try:
        qf = queuefile_mod.parse(path)
    except OSError:
        return (None, False)
    if qf.malformed:
        return (None, False)
    return (qf.front_matter.get("form"), True)


def comparability(post: dict) -> dict:
    """**比較材料に採用してよいか**（masaru 裁定 2026-09-12・3 番／出口条件 §3）。

    **投稿できる条件と、比較材料に採用できる条件は別。** 型が無くても投稿は
    止めない（裁定 3 番・`forms.missing_form_warning` は警告のまま）。**比較の
    分母に入れるかどうかは、ここで別に決める。**

    戻り値の `ok` は **3 値**:

    - `False` … **採用できない**（`blockers` に理由）
    - `None`  … **判らない**（`unchecked` が空でない。**「採用してよい」ではない**）
    - `True`  … 採用してよい（**`unchecked` が空のときだけ**）

    **確かめていない条件を、満たしたことにしない。** いま機械で見られるのは
    3 つだけで、**残り 2 つは見る口が無い**——それを黙って通すと、
    「宣言だけで条件が守られた」ことになる（masaru 裁定 5 番と同じ筋）。
    """
    blockers = []
    unchecked = []
    if not post.get("form_readable"):
        # **読めなかったことを「無い」と言わない**（外部レビュー C2）。
        unchecked.append("型を確認できない（原稿を読めない・不存在・不正形式）")
    elif not post.get("form_now"):
        blockers.append("型（`form`）が無い")
    if not post.get("post_id"):
        blockers.append("公開されていない")
    rows = post.get("rows") or []
    if not rows:
        blockers.append("実測が 1 行も無い")
    elif all(r.get("missing") for r in rows):
        blockers.append("実測が揃っている行が 1 つも無い")

    # **見る口が無いもの。** 空にできるまで `ok` は `True` にならない。
    unchecked += [
        "修正理由が記録されているか（投稿と `reason` を結ぶ口がまだ無い）",
        "型が後から付けられたものでないか（型の記録の時刻を投稿と比べる口がまだ無い）",
    ]
    ok = False if blockers else None
    return {"ok": ok, "blockers": blockers, "unchecked": unchecked}


def _mark_collapsed(rows: list) -> None:
    """刻みが同居している行に印を付ける（`rows` を書き換える）。

    **理由**: 25 時間後の 1 回の取得に `1h`・`6h`・`24h` の印が付いても、
    過去 3 時点を復元したわけではない。実際に測ったのは 1 点。「n 点測った」と
    読ませないための印。
    """
    for row in rows:
        marks = row.get("marks") or []
        row["marks_collapsed"] = len(marks) >= 2


def load(account_name: str, *, observation_metadata: bool = False) -> dict:
    """1 つの account の実測を台帳から機械的に並べる。

    **account をまたいで並べない。** ここは 1 account だけを扱う。複数 account を
    1 つの表にまとめる関数はここに作らない——混ぜると「型の差」と「アカウントの
    地力の差」が分離できなくなる（設計 §2 の表・§12.3 と同じ理由。
    `account_report.measured_views_by_account()` 参照）。

    戻り値:
      - `posts`: **この account が所有すると根拠付きで判った**投稿ごとの配列
        （1 投稿 1 要素）。`post_id`・`topic`・`form_now`（いまの queue
        ファイルから引いた値。過去の型ではない）・`form_source`
        （`"current_draft"` 固定。`form_now` の由来を明示する）・`file`・
        `posted_at`・`rows_unattributed`（採取時点の `account` が無く、この
        系列から**外した**行の数。0 でなければ時系列は途中から始まっている）
        と、時系列の `rows`（`collected_at`・`age_hours`・`marks`・
        `marks_collapsed`・`metrics`）を持つ。**`rows` はこの account の
        `account` を持つ行だけ。**
      - `posts_unknown_ownership`: 所有 account を**判別できなかった**投稿
        台帳の post_id（**裏付けのある行が 1 行も無い**場合——R3 より前に
        採取した行だけ、または壊れた採取）。この account の実測へ推定で
        混ぜず、ここに分けて出す——`posts` にも他 account の分にも入らない。
        **現在の原稿の account では復元しない**（R3）。
      - `account_daily`: アカウント日次の行（`date` と `metrics`）。ファイル名
        `<account>-<年月>.ndjson` が一致するものだけ（M3）。
      - `broken`: 読めなかった・信用できなかったファイルの名前（壊れと
        不存在を混ぜない。ファイル名の account と中身の `account` が食い違う
        場合もここに入る）。
      - **`marks` は「どの刻みとして採ったか」であって、経過時間ではない。**
        timer は 10 分刻みで走り、刻みは投稿の秒に固定されているので、
        **刻みは常に最大 10 分遅れて拾われる**（実例: 6h の刻みが age=6.16 で
        採れた・2026-09-12 運用セッション観測。逆に 29 秒足りずに 1 周期
        ずれたこともある）。**「6h の値」と丸めて読まない。** 判断には
        `age_hours` の実値を使うこと。
      - `missing_post_metrics`: **投稿単位の台帳で** 1 度も現れていない指標の
        名前（他 account の行を含めずに判定する）。**所有の裏付けがある投稿が
        1 件も無ければ `None`**——「欠けている」ではなく「まだ 1 件も無い」。
      - `missing_account_daily_metrics`: **アカウント日次の台帳で** 1 度も
        現れていない指標の名前。**日次の台帳が 1 本も無ければ `None`**——
        「欠けている」ではなく「採っていないので判らない」。空配列（台帳は
        あるが欠けは無い）と混ぜない。
        **層をまたいで数えない**（取れる指標が層ごとに違う。`shares` は投稿
        にしか無く、`clicks`・`followers_count` はアカウントにしか無い）。
      - 上の 2 つは「**1 度も**現れていない」の判定なので、**採取の版が上がる
        と、翌日から入った指標が前日の欠測を隠す。** そこで `posts[].rows[]` と
        `account_daily[]` の**各行にも `missing`**（その行に無い指標の名前）を
        付ける。`_row_missing()` の docstring に実例がある。

    読むだけ。何も書かない。
    """
    account_cfg = accounts_mod.load_account(account_name)
    repo_dir = account_cfg.get("repo_dir") or ""
    queue_dir = os.path.join(repo_dir, account_cfg.get("queue_dir") or "")
    # **置き場の解決は 1 か所**（設計 v2.0.1 §1・`accounts.data_dirs()`）。
    # repo が無い account（同席専用・`thth send` だけで出す）では
    # `$THTH_ROOT/state/<account>/data/sns/…` を読む——**直書きが 1 か所でも
    # 残ると、そこだけ別の場所を見る。**
    dirs = accounts_mod.data_dirs(account_cfg, account_name)
    posts_dir = dirs["insights_posts"]
    account_daily_dir = dirs["insights_account"]

    broken: list = []
    posts: list = []
    unknown_posts: list = []
    # **層をまたいで数えない。** 投稿単位で見た指標名と、アカウント日次で見た
    # 指標名を別々に持つ（上の一覧の説明を参照）。
    seen_post_metrics: set = set()
    seen_daily_metrics: set = set()

    if os.path.isdir(posts_dir):
        for name in sorted(os.listdir(posts_dir)):
            if not name.endswith(".ndjson"):
                continue
            rows, is_broken = _read_ndjson(os.path.join(posts_dir, name))
            if is_broken:
                broken.append(name)
                continue
            if not rows:
                continue

            # ファイル名は percent-encode してある（`thth/postid.py`・T3
            # 2026-09-13）。Threads の数字だけの名前は素通りする。
            post_id = postid_mod.from_filename(name[: -len(".ndjson")])
            # **所有 account は行そのものの `account` だけを根拠にする**
            # （R3・2026-09-12）。以前（M3）は行に `account` が無いことを
            # 前提に、`file` を手がかりに queue_dir の**現在の**原稿を開いて
            # その account を過去の所有として使っていた。だが現在の原稿の
            # account は「いま」の値であって「採取時点」の値ではない——
            # 原稿の account を書き換えると、過去の台帳が現在の account の
            # 実測へ黙って移し替えられてしまう。**裏付けの無いものを、推定で
            # 混ぜない。**
            #
            # **選別は行ごとに行う**（R3 が残した制限を閉じる・2026-09-12）。
            # R3 の実装は**先頭行 1 行**で 1 ファイルの所有を決めていた。
            # 先頭行が R3 より前（`account` 無し）だと、後から `account` 付きの
            # 行がいくら足されても、その投稿は永久に「不明」のままになる——
            # 実際、kopicha の 6 投稿すべてがその状態で、道具が空で出ていた。
            # 行ごとに見れば、裏付けのある行は裏付けのあるまま出せる。
            # **足りない分は「無い」ではなく「不明」として数を出す**
            # （`rows_unattributed`）——時系列の頭が欠けているのに、そこから
            # 始まったかのように読ませない。
            owners = {r.get("account") for r in rows if r.get("account") is not None}
            if len(owners) > 1:
                # 1 つの post_id が 2 つの account に属することは実際には
                # 起こらない。起きているならこのファイルは信用できない
                # （取り違え・import/merge・改竄）——**1 行も使わない**。
                broken.append(name)
                continue
            own_rows = [r for r in rows if r.get("account") == account_name]
            unattributed = [r for r in rows if r.get("account") is None]
            if not own_rows:
                if owners:
                    # 他 account と判っている。不明ではなく、単に自分のではない。
                    continue
                # 裏付けのある行が 1 行も無い——不明（R3 より前の行だけ、
                # または壊れた採取）。この account かもしれないが判別できない
                # ので、推定で混ぜない。
                unknown_posts.append(post_id)
                continue

            _mark_collapsed(own_rows)
            own_rows.sort(key=lambda r: r.get("collected_at") or "")
            for row in own_rows:
                seen_post_metrics.update((row.get("metrics") or {}).keys())

            first = own_rows[0]
            # **1 回の戻り値を 2 欄へ分ける**（外部レビュー・2026-09-12）。
            # 2 回呼んでいたので、**途中で原稿が現れる／消えると
            # `form_now` と `form_readable` が食い違った。**
            型, 読めた = _form_state(queue_dir, first.get("file"))
            posts.append({
                "post_id": post_id,
                "topic": first.get("topic"),
                # **採取時点に帰属する 2 つ**（`thth/collect.py` が行に書いている）。
                # `thth/ask.py`（設計 v2 §1）がここから読む——`medium` は
                # 「媒体をまたいで比較しない」（設計 v2 §2.1）ため、`reply_to` は
                # 「返信かどうか」で群を分けるため。**現在の原稿からは引かない**
                # （`form_now` と違い、行そのものに残っている値）。
                "medium": first.get("medium"),
                "reply_to": first.get("reply_to"),
                # `None` だけでは「根」と「古い行に鍵が無く不明」を区別できない。
                # `after_you_posted` が自分の根投稿だけを数えるため、採取時点の行に
                # 鍵そのものがあったかも残す（現在の原稿からは補わない）。
                "reply_to_known": "reply_to" in first,
                "form_now": 型,
                # **読めたかどうかを別に持つ。** `form_now` の `None` だけでは
                # 「型が無い」と「読めなかった」を区別できない（外部レビュー C2）。
                "form_readable": 読めた,
                "form_source": "current_draft",
                # **どの記録から採った実測か**（設計 v2.0.1 §3・設計 v1 §3.2.2）。
                # `"queue"` は書き戻された front-matter（不在の様態・原稿がある）、
                # `"sent"` は `state/<account>/sent/`（同席の様態・原稿は無い）。
                # **無印の古い行は `queue`** ——`source` を書き始めたのは
                # 2026-09-14 で、それ以前の行はすべて queue 由来。
                "source": first.get("source") or "queue",
                "file": first.get("file"),
                "posted_at": first.get("posted_at"),
                # 採取時点の account が無く、この投稿の系列から**外した**行の数。
                # 0 でなければ時系列は途中から始まっている。
                "rows_unattributed": len(unattributed),
                "rows": [
                    {
                        **({"posted_at": row.get("posted_at"),
                            "reply_to": row.get("reply_to"),
                            "reply_to_known": "reply_to" in row}
                           if observation_metadata else {}),
                        "collected_at": row.get("collected_at"),
                        "age_hours": row.get("age_hours"),
                        "marks": row.get("marks"),
                        "marks_collapsed": row.get("marks_collapsed", False),
                        "source": row.get("source") or "queue",
                        "metrics": row.get("metrics"),
                        # **この行に無い指標**（採取の版が上がる前の行かどうかが
                        # 読める）。全体の判定では翌日の行に隠される。
                        "missing": _row_missing(row.get("metrics"), POST_METRIC_NAMES),
                    }
                    for row in own_rows
                ],
            })
            # **比較材料に採用してよいか**（裁定 3 番）。投稿の可否とは別の欄。
            posts[-1]["comparable"] = comparability(posts[-1])

    account_daily: list = []
    # **日次の台帳が 1 本も無いのと、あるのに指標が欠けているのは違う。**
    # 1 本も無いなら「欠けている」と言わない——採っていないので、欠けて
    # いるかどうかも判らない（壊れと不存在を混ぜないのと同じ理由）。
    daily_files_seen = 0
    # **アカウント日次はファイル名そのものが根拠**（`thth/collect.py` の
    # `_collect_account_daily()` が `<account>-<年月>.ndjson` で書く）。
    # 一致しないファイルは他 account と判っているので、不明にはせず、ただ除く。
    daily_name_re = re.compile(r"^" + re.escape(account_name) + r"-\d{4}-\d{2}\.ndjson$")
    if os.path.isdir(account_daily_dir):
        for name in sorted(os.listdir(account_daily_dir)):
            if not name.endswith(".ndjson"):
                continue
            if not daily_name_re.match(name):
                continue
            daily_files_seen += 1
            rows, is_broken = _read_ndjson(os.path.join(account_daily_dir, name))
            if is_broken:
                broken.append(name)
                continue
            # ファイル名の account と、行の中身の `account` が食い違う行が
            # あれば、このファイルは信用できない（ファイル名だけを根拠に
            # 混ぜない）——壊れとして扱う。
            if any(r.get("account") not in (None, account_name) for r in rows):
                broken.append(name)
                continue
            for row in rows:
                seen_daily_metrics.update((row.get("metrics") or {}).keys())
                account_daily.append({
                    "date": row.get("date"), "metrics": row.get("metrics"),
                    "missing": _row_missing(row.get("metrics"),
                                            ACCOUNT_DAILY_METRIC_NAMES),
                })

    # **投稿が 1 件も無いのと、あるのに指標が欠けているのは違う**（運用指摘
    # 2026-09-12）。日次で先に分けた区別（`None` = 台帳が無い）を、投稿側でも
    # 揃える。**「投稿が 1 件も無い」を「6 つの指標が欠けている」と出していた**
    # ——`—— 投稿 0 件` と並ぶので読めはするが、**事実に近いのは「まだ 1 件も
    # 無い」のほう。**
    missing_post_metrics = (
        [m for m in POST_METRIC_NAMES if m not in seen_post_metrics]
        if posts else None)
    # 台帳が 1 本も無ければ `None`（「欠けている」ではなく「判らない」）。
    missing_daily_metrics = (
        [m for m in ACCOUNT_DAILY_METRIC_NAMES if m not in seen_daily_metrics]
        if daily_files_seen else None)

    posts.sort(key=lambda p: p["post_id"])
    unknown_posts.sort()
    account_daily.sort(key=lambda r: r.get("date") or "")

    return {
        "account": account_name,
        "posts": posts,
        "posts_unknown_ownership": unknown_posts,
        "account_daily": account_daily,
        "broken": sorted(broken),
        "missing_post_metrics": missing_post_metrics,
        # **`None` は「欠けていない」ではなく「日次の台帳が 1 本も無いので
        # 判らない」。** 空配列（＝台帳はあり、欠けは無い）と混ぜないこと。
        "missing_account_daily_metrics": missing_daily_metrics,
    }
