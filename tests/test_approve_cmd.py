"""`thth approve`（外部レビュー §1・受け入れ 1〜5）。

承認を「見た本文」に結び付ける: `thth approve` が `approved_sha`（本文＋account＋
reply_to＋topic＋publish_at の hash）を front-matter に書き、`select` はそれが
いまの内容と一致するときだけ通す。承認後に中身を書き換えると `approval_stale` で
落ちる（黙って出さない）。手で `status: approved` とだけ書いた（`approved_sha` 無し）
ファイルも同じ扱い。`thth approve` 自身は lint を通らないファイルを承認しない。
"""
from __future__ import annotations

import datetime

from tests.conftest import approve_via_cli, run_thth, write_queue_file
from thth import core
from thth import jst
from thth import queuefile
from thth import select as select_mod


def _patch_front_matter_field(path: str, key: str, value) -> None:
    """front-matter の 1 行だけを「利用者が手で書き換えた」体で差し替える
    （approved_sha は再計算しない——これが「見た本文と違う」を作るための細工）。
    """
    with open(path, encoding="utf-8") as f:
        lines = f.read().split("\n")
    assert lines[0].strip() == "---"
    end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    for i in range(1, end):
        if lines[i].split(":", 1)[0].strip() == key:
            lines[i] = f"{key}: {value if value is not None else ''}"
            break
    else:
        lines.insert(end, f"{key}: {value if value is not None else ''}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _rewrite_body(path: str, old: str, new: str) -> None:
    with open(path, encoding="utf-8") as f:
        text = f.read()
    assert old in text
    with open(path, "w", encoding="utf-8") as f:
        f.write(text.replace(old, new))


# 受け入れ 5: lint に通らないファイルを承認しない。
def test_5_lintに通らないファイルは承認しない(isolated_account):
    # publish_at に +09:00 が無い → lint エラー
    path = write_queue_file(
        isolated_account["queue_dir"], "a.md",
        fm_overrides={"status": "draft", "publish_at": "2026-09-09T08:00:00",
                      "approved_sha": None})
    # 一段目で断られる（本文も digest も出ない）ので、二段確認のヘルパは通さない。
    result = run_thth(["approve", path])
    assert result.returncode == 1
    assert "digest:" not in result.stdout
    qf = queuefile.parse(path)
    assert qf.front_matter.get("status") == "draft"  # 書き換わっていない
    assert not qf.front_matter.get("approved_sha")


def test_approveは正常なファイルにapproved_shaとapproved_atを書く(isolated_account):
    path = write_queue_file(isolated_account["queue_dir"], "a.md",
                            fm_overrides={"status": "draft", "approved_sha": None})
    result = approve_via_cli(path)
    assert result.returncode == 0
    qf = queuefile.parse(path)
    assert qf.front_matter.get("status") == "approved"
    assert qf.front_matter.get("approved_sha")
    assert len(qf.front_matter["approved_sha"]) == 64
    assert qf.front_matter.get("approved_at")


# 受け入れ 3: 承認 → 何も変えない → 出る。
def test_3_承認後に何も変えなければ選ばれる(isolated_account):
    path = write_queue_file(isolated_account["queue_dir"], "a.md",
                            fm_overrides={"status": "draft", "approved_sha": None})
    approve = approve_via_cli(path)
    assert approve.returncode == 0

    result = core.throw_once(isolated_account["name"])
    assert result.action == "skip"  # dry-run: 選ばれてログに出すだけ
    assert result.file == path


# 受け入れ 1: 承認 → 本文を書き換える → 出ない（approval_stale）・board に出る。
def test_1_承認後に本文を書き換えると出ない(isolated_account):
    path = write_queue_file(isolated_account["queue_dir"], "a.md",
                            fm_overrides={"status": "draft", "approved_sha": None})
    approve_via_cli(path)
    _rewrite_body(path, "本文です。", "書き換えた本文です。")

    result = core.throw_once(isolated_account["name"])
    assert result.action == "none"

    # needs_review（board 側）に入る・理由は approval_stale。
    qf = queuefile.parse(path)
    account_cfg = {"media": "threads", "hashtags": True, "quiet_hours": None,
                   "min_interval_hours": 0, "stale_days": 7}
    import datetime
    sel = select_mod.select_one(
        [qf], account_name=isolated_account["name"], account_cfg=account_cfg,
        now=datetime.datetime.fromisoformat("2026-09-09T10:00:00+09:00"),
        last_post_at=None, recent_texts=set())
    assert sel.chosen is None
    assert any(r.reason == "approval_stale" for r in sel.rejections)
    assert path in sel.needs_review


# 受け入れ 2: topic だけ変える → 出ない。
def test_2_承認後にtopicだけ変えると出ない(isolated_account):
    path = write_queue_file(isolated_account["queue_dir"], "a.md",
                            fm_overrides={"status": "draft", "approved_sha": None})
    approve_via_cli(path)
    _patch_front_matter_field(path, "topic", "苦味")

    result = core.throw_once(isolated_account["name"])
    assert result.action == "none"


# 受け入れ 2: reply_to だけ変える → 出ない。
def test_2_承認後にreply_toだけ変えると出ない(isolated_account):
    path = write_queue_file(isolated_account["queue_dir"], "a.md",
                            fm_overrides={"status": "draft", "approved_sha": None})
    approve_via_cli(path)
    _patch_front_matter_field(path, "reply_to", "9999999")

    result = core.throw_once(isolated_account["name"])
    assert result.action == "none"


# 受け入れ 2: publish_at だけ変える → 出ない。
def test_2_承認後にpublish_atだけ変えると出ない(isolated_account):
    path = write_queue_file(isolated_account["queue_dir"], "a.md",
                            fm_overrides={"status": "draft", "approved_sha": None})
    approve_via_cli(path)
    # 承認時と別の時刻（まだ過去＝選ばれる資格はある）に書き換える。
    _patch_front_matter_field(path, "publish_at", "2026-09-09T07:00:00+09:00")

    result = core.throw_once(isolated_account["name"])
    assert result.action == "none"


# 受け入れ 4: 手で status: approved と書いただけ（approved_sha 無し）→ 出ない。
def test_4_approved_sha無しの手書きapprovedは出ない(isolated_account):
    write_queue_file(isolated_account["queue_dir"], "a.md",
                     fm_overrides={"status": "approved", "approved_sha": None})
    result = core.throw_once(isolated_account["name"])
    assert result.action == "none"


def test_approveはpost_idが付いていると承認しない(isolated_account):
    path = write_queue_file(
        isolated_account["queue_dir"], "a.md",
        fm_overrides={"status": "posted", "post_id": "12345",
                      "posted_at": "2026-09-08T08:00:00+09:00", "approved_sha": None})
    result = run_thth(["approve", path])
    assert result.returncode == 1
    assert "digest:" not in result.stdout


# --- 二段確認（masaru 指示 2026-09-10「AI との対話の中から承認できるようにしたい」）

def test_一段目は本文を全文見せて何も書き換えない(isolated_account):
    path = write_queue_file(isolated_account["queue_dir"], "a.md",
                            fm_overrides={"status": "draft", "approved_sha": None})
    first = run_thth(["approve", path])

    assert first.returncode == 1
    assert "本文です。" in first.stdout, first.stdout      # 出す本文がそのまま出る
    assert "digest: " in first.stdout
    assert "--confirm" in first.stdout                     # 次に打つ形まで見せる
    qf = queuefile.parse(path)
    assert qf.front_matter.get("status") == "draft"        # 書き換わっていない
    assert not qf.front_matter.get("approved_sha")


def test_見せた本文と違うものは承認できない(isolated_account):
    """**この 1 件がこの仕掛けの理由。**

    AI が勝手に承認することは防げない（Bash も ssh も持っている）。防げるのは
    **「A を見せて B を承認する」**ほう——表示と承認の間に本文が変われば digest が
    変わり、二段目が通らない。
    """
    path = write_queue_file(isolated_account["queue_dir"], "a.md",
                            fm_overrides={"status": "draft", "approved_sha": None})
    first = run_thth(["approve", path])
    digest = next(l.split(": ", 1)[1].strip() for l in first.stdout.splitlines()
                  if l.startswith("digest: "))

    _rewrite_body(path, "本文です。", "こっそり差し替えた本文です。")
    second = run_thth(["approve", path, "--confirm", digest])

    assert second.returncode == 1
    assert "digest が一致しない" in second.stderr, second.stderr
    qf = queuefile.parse(path)
    assert qf.front_matter.get("status") == "draft"        # 承認されていない


def test_誰が承認したかを残す(isolated_account):
    path = write_queue_file(isolated_account["queue_dir"], "a.md",
                            fm_overrides={"status": "draft", "approved_sha": None})
    result = approve_via_cli(path, by="claude（nigamilab セッション）")
    assert result.returncode == 0

    qf = queuefile.parse(path)
    assert qf.front_matter.get("approved_by") == "claude（nigamilab セッション）"

    import subprocess
    log = subprocess.run(["git", "-C", isolated_account["repo_dir"], "log", "--oneline", "-1"],
                          capture_output=True, text=True).stdout
    assert "claude（nigamilab セッション）" in log, log


# --- 複数本まとめて（asmon 関東セッション指摘 2026-09-10）

def test_複数本を束のdigestでまとめて承認できる(isolated_account):
    """47 本の連載で一段目 94 回・二段目 47 回になった、という報告への答え。"""
    paths = [write_queue_file(isolated_account["queue_dir"], f"{i}.md",
                              fm_overrides={"status": "draft", "approved_sha": None},
                              body=f"## threads\n\n{i} 本目です。\n")
             for i in range(1, 4)]

    first = run_thth(["approve", *paths])
    assert first.returncode == 1
    for i in range(1, 4):
        assert f"{i} 本目です。" in first.stdout, first.stdout   # 全部の本文が出る
    bundle = next(l.split(": ", 1)[1].strip() for l in first.stdout.splitlines()
                  if l.startswith("束の digest: "))

    second = run_thth(["approve", *paths, "--confirm", bundle, "--by", "kanto"])
    assert second.returncode == 0, second.stderr
    for path in paths:
        assert queuefile.parse(path).front_matter.get("status") == "approved"


def test_1本でも変われば束のdigestが変わる(isolated_account):
    """束にしても「見せたもの＝承認したもの」の保証は崩れない。"""
    paths = [write_queue_file(isolated_account["queue_dir"], f"{i}.md",
                              fm_overrides={"status": "draft", "approved_sha": None},
                              body=f"## threads\n\n{i} 本目です。\n")
             for i in range(1, 4)]
    first = run_thth(["approve", *paths])
    bundle = next(l.split(": ", 1)[1].strip() for l in first.stdout.splitlines()
                  if l.startswith("束の digest: "))

    _rewrite_body(paths[1], "2 本目です。", "こっそり差し替えた 2 本目です。")
    second = run_thth(["approve", *paths, "--confirm", bundle])

    assert second.returncode == 1
    assert "digest が一致しない" in second.stderr
    for path in paths:
        assert queuefile.parse(path).front_matter.get("status") == "draft"


def test_1本でも駄目なら1本も承認しない(isolated_account):
    """半分だけ承認された状態を作らない。"""
    ok1 = write_queue_file(isolated_account["queue_dir"], "1.md",
                           fm_overrides={"status": "draft", "approved_sha": None})
    bad = write_queue_file(isolated_account["queue_dir"], "2.md",
                           fm_overrides={"status": "draft", "approved_sha": None,
                                         "publish_at": "2026-09-09T08:00:00"})  # tz 無し
    result = run_thth(["approve", ok1, bad])
    assert result.returncode == 1
    assert "1 本も承認しませんでした" in result.stderr, result.stderr
    assert queuefile.parse(ok1).front_matter.get("status") == "draft"


def test_lintは複数本を一度に見る(isolated_account):
    ok1 = write_queue_file(isolated_account["queue_dir"], "1.md")
    bad = write_queue_file(isolated_account["queue_dir"], "2.md",
                           fm_overrides={"publish_at": "2026-09-09T08:00:00"})
    result = run_thth(["lint", ok1, bad])
    assert result.returncode == 1
    assert "1.md: OK" in result.stdout, result.stdout
    assert "2.md: " in result.stdout


# --- 予定時刻を過ぎた原稿（nigamilab セッション指摘 2026-09-10）

def test_予定時刻を過ぎていたら承認の前にすぐ出ると言う(isolated_account):
    """起草する人と承認する人が別なので、承認までに時刻が過ぎるのは普通に起きる。
    「承認したらいつ出るのか」を知らないまま押す形にしない。

    `run_thth()` はサブプロセスなので `frozen_now_jst` が届かない（conftest 参照）。
    固定の日付を書くと、壁時計がその日から `stale_days`（既定 7 日）を超えて
    進んだ時点でこのテストが「承認しても出ません」に化けて落ちる（2026-09-16 に
    発見）。実の壁時計（`jst.now_jst()` は monkeypatch 済みなので使わず、
    `datetime.datetime.now()` から直接組む）からの相対（2 時間前）で作れば、
    いつテストを走らせても「過ぎてはいるが stale_days は超えていない」が保たれる。
    """
    real_now = datetime.datetime.now(tz=datetime.timezone.utc).astimezone(jst.JST)
    publish_at = (real_now - datetime.timedelta(hours=2)).isoformat(timespec="seconds")
    path = write_queue_file(isolated_account["queue_dir"], "a.md", fm_overrides={
        "status": "draft", "approved_sha": None,
        "publish_at": publish_at})
    first = run_thth(["approve", path])
    assert "承認するとすぐ出ます" in first.stdout, first.stdout


def test_stale_daysを超えていたら承認しても出ないと言う(isolated_account):
    path = write_queue_file(isolated_account["queue_dir"], "a.md", fm_overrides={
        "status": "draft", "approved_sha": None,
        "publish_at": "2026-08-20T08:00:00+09:00"})  # 20 日前（stale_days 既定 7）
    first = run_thth(["approve", path])
    assert "承認しても出ません" in first.stdout, first.stdout
    assert "stale_days" in first.stdout


def test_これから出るものには注意を出さない(isolated_account):
    # `run_thth` はサブプロセスなので frozen_now_jst が届かない（conftest 参照）。
    # 実の壁時計より確実に先の時刻を使う。
    path = write_queue_file(isolated_account["queue_dir"], "a.md", fm_overrides={
        "status": "draft", "approved_sha": None,
        "publish_at": "2030-01-01T08:00:00+09:00"})
    first = run_thth(["approve", path])
    assert "承認するとすぐ出ます" not in first.stdout
    assert "承認しても出ません" not in first.stdout


def test_byを省いたら承認しない(isolated_account):
    """kopicha セッション指摘 2026-09-10: `--by` を省いたら 28 本に
    `approved_by: wt`（VM の unix ユーザー名）が入った。判断したのは masaru なのに
    記録は `wt`。**承認は THTH がいちばん重く扱っている一線**なのだから、
    誰が承認したか判らないまま通してはいけない。既定を作らず、名乗らせる。
    """
    path = write_queue_file(isolated_account["queue_dir"], "a.md",
                            fm_overrides={"status": "draft", "approved_sha": None})
    first = run_thth(["approve", path])
    digest = next(l.split(": ", 1)[1].strip() for l in first.stdout.splitlines()
                  if l.startswith("digest: "))

    result = run_thth(["approve", path, "--confirm", digest])  # --by なし
    assert result.returncode == 1
    assert "--by を付けてください" in result.stderr, result.stderr
    assert queuefile.parse(path).front_matter.get("status") == "draft"


def test_THTH_ACTORでも名乗れる(isolated_account):
    path = write_queue_file(isolated_account["queue_dir"], "a.md",
                            fm_overrides={"status": "draft", "approved_sha": None})
    first = run_thth(["approve", path])
    digest = next(l.split(": ", 1)[1].strip() for l in first.stdout.splitlines()
                  if l.startswith("digest: "))
    result = run_thth(["approve", path, "--confirm", digest],
                      env={"THTH_ACTOR": "masaru"})
    assert result.returncode == 0, result.stderr
    assert queuefile.parse(path).front_matter.get("approved_by") == "masaru"
