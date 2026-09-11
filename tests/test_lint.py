"""`thth lint`（発注 §5 受け入れ 1）。正しい fixture は 0、7 つの事故はそれぞれ理由を名指しして 1。"""
from __future__ import annotations

import os

from tests.conftest import FIXTURES_DIR, make_queue_text, run_thth, write_queue_file
from thth import lint as lint_mod


def test_umami_bile_fixtureは検査を通る(isolated_account):
    path = os.path.join(FIXTURES_DIR, "umami-bile.md")
    errors = lint_mod.lint_file(path)
    assert errors == []


def test_thth欠落は理由を名指しする(isolated_account, tmp_path):
    path = write_queue_file(str(tmp_path), "bad.md", omit=("thth",))
    errors = lint_mod.lint_file(path)
    assert any(e.startswith("thth:") for e in errors)


def test_publish_atにplus0900が無いのを名指しする(isolated_account, tmp_path):
    path = write_queue_file(str(tmp_path), "bad.md",
                             fm_overrides={"publish_at": "2026-09-09T08:00:00"})
    errors = lint_mod.lint_file(path)
    assert any("publish_at" in e and "+09:00" in e for e in errors)


def test_publish_atが壊れた文字列なのを名指しする(isolated_account, tmp_path):
    path = write_queue_file(str(tmp_path), "bad.md",
                             fm_overrides={"publish_at": "2026-99-99T99:99:00+09:00"})
    errors = lint_mod.lint_file(path)
    assert any("publish_at" in e and "形式" in e for e in errors)


def test_媒体の節が無いのを名指しする(isolated_account, tmp_path):
    path = write_queue_file(str(tmp_path), "bad.md", body="本文だけで節が無い\n")
    errors = lint_mod.lint_file(path)
    assert any(e.startswith("media:") for e in errors)


def test_501字を名指しする(isolated_account, tmp_path):
    # 文字数は前後の空白を落とした本文そのもの（末尾改行は数えない・T1 検収
    # 2026-09-09 で確定）。501 字は通常文字だけで作る。
    body = "## threads\n\n" + ("あ" * 501) + "\n"
    path = write_queue_file(str(tmp_path), "bad.md", body=body)
    errors = lint_mod.lint_file(path)
    assert any(e.startswith("length:") and "501" in e for e in errors)


def test_450字を超えたら警告するが落とさない(isolated_account, tmp_path):
    body = "## threads\n\n" + ("あ" * 451) + "\n"
    path = write_queue_file(str(tmp_path), "bad.md", body=body)
    errors = lint_mod.lint_file(path)
    warnings = [e for e in errors if lint_mod.is_warning(e)]
    real_errors = [e for e in errors if not lint_mod.is_warning(e)]
    assert any("451" in w for w in warnings)
    assert real_errors == []


def test_statusが未知の語なのを名指しする(isolated_account, tmp_path):
    path = write_queue_file(str(tmp_path), "bad.md", fm_overrides={"status": "hoge"})
    errors = lint_mod.lint_file(path)
    assert any(e.startswith("status:") and "hoge" in e for e in errors)


def test_hashtagを含む本文を名指しする(isolated_account, tmp_path):
    path = write_queue_file(str(tmp_path), "bad.md", body="## threads\n\n本文 #タグ です\n")
    errors = lint_mod.lint_file(path)
    assert any(e.startswith("hashtag:") for e in errors)


def test_topic省略は従来どおり通る(isolated_account, tmp_path):
    path = write_queue_file(str(tmp_path), "ok.md")
    errors = lint_mod.lint_file(path)
    assert errors == []


def test_正常なtopicは通る(isolated_account, tmp_path):
    path = write_queue_file(str(tmp_path), "ok.md", fm_overrides={"topic": "苦味"})
    errors = lint_mod.lint_file(path)
    assert errors == []


def test_topicが51字を名指しする(isolated_account, tmp_path):
    path = write_queue_file(str(tmp_path), "bad.md", fm_overrides={"topic": "あ" * 51})
    errors = lint_mod.lint_file(path)
    assert any(e.startswith("topic_too_long") for e in errors)


def test_topicにピリオドを含むのを名指しする(isolated_account, tmp_path):
    path = write_queue_file(str(tmp_path), "bad.md", fm_overrides={"topic": "苦味."})
    errors = lint_mod.lint_file(path)
    assert "topic_invalid_char(.)" in errors


def test_topicにアンパサンドを含むのを名指しする(isolated_account, tmp_path):
    path = write_queue_file(str(tmp_path), "bad.md", fm_overrides={"topic": "苦味&旨味"})
    errors = lint_mod.lint_file(path)
    assert "topic_invalid_char(&)" in errors


def test_topicの先頭のシャープを落として通る(isolated_account, tmp_path):
    # 人が `#苦味` と書きがち。ハッシュタグとは別物なので `#` を機械的に外す（設計 §4.1）。
    path = write_queue_file(str(tmp_path), "ok.md", fm_overrides={"topic": "#苦味"})
    errors = lint_mod.lint_file(path)
    assert errors == []


def test_空ディレクトリを渡すと非ゼロで理由が出る(tmp_path, isolated_account):
    """バグ 2: 空ディレクトリを渡すと `_expand_targets()` が空を返し、for が 0 回
    まわって非 JSON では無言、`--json` でも `[]` を出して、どちらも exit 0 だった
    （監査 2026-09-11・掃討で検出）。「0 本を検査した」がどこにも出ないまま
    正常終了に見えてしまう事故を防ぐ。
    """
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()

    result = run_thth(["lint", str(empty_dir)])
    assert result.returncode != 0, result.stdout
    assert "0" in result.stderr and "検査" in result.stderr

    result_json = run_thth(["lint", str(empty_dir), "--json"])
    assert result_json.returncode != 0, result_json.stdout
    assert "検査" in result_json.stderr
