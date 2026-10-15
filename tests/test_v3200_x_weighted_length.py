"""X の本文を X の公式の数え方（twitter-text v3 の weighted length）で数える（3.20.0）。

起きたこと（10/15・kopicha-x）: send は「文字数: 160/280（x・codepoint（暫定・重み
付け未確認））」と出して通したが、X のアプリに貼ると 284/280 だった。CJK・全角は
X では 1 字で 2 と数える。

ここで固定するもの:
  - twitter-text の conformance（validate.yml の v3 の重み付き計数・extract.yml の
    URL の抜き出し）を全件写した `tests/fixtures/twitter_text_v3_weighted.json` と一致する
  - lint・approve・send（dry-run）・スレッド（bundle.check）・select が、同じ本文を
    **止める**（警告でなく）。表示は「284/280（X の重み付き）」
  - ほかの媒体の数え方は変えない
"""
from __future__ import annotations

import datetime
import json
import os

import pytest

from tests.conftest import make_queue_text
from thth import approval, bundle, cli, core, lint as lint_mod, queuefile
from thth import select as select_mod
from thth.adapters import x as x_adapter, x_text

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "twitter_text_v3_weighted.json")
with open(FIXTURE, encoding="utf-8") as _f:
    CONFORMANCE = json.load(_f)

NOW = datetime.datetime.fromisoformat("2026-10-15T10:00:00+09:00")
# kopicha-x の形: コードポイントでは 160、X の重み付きでは 124×2＋36＝284。
OVER = "あ" * 124 + "a" * 36
# ちょうど 280（全角 140）は通る。
EDGE = "あ" * 140


# ---------------------------------------------------------------- 数え方そのもの

@pytest.mark.parametrize("case", CONFORMANCE["weighted"], ids=lambda c: c["description"][:60])
def test_conformanceの重み付きの長さと一致する(case):
    assert x_text.weighted_length(case["text"]) == case["weightedLength"]


@pytest.mark.parametrize("case", CONFORMANCE["urls"], ids=lambda c: c["description"][:60])
def test_conformanceのURLの抜き出しと一致する(case):
    units = x_text._utf16(case["text"])
    got = [x_text._from_utf16(url) for url, _start, _end in x_text.extract_urls(units)]
    assert got == case["urls"]


def test_日本語と全角は2_URLは23_絵文字の並びは2():
    assert x_text.weighted_length("あ") == 2
    assert x_text.weighted_length("Ａ") == 2          # 全角英字
    assert x_text.weighted_length("、。") == 4
    assert x_text.weighted_length("abc") == 3
    assert x_text.weighted_length("“”…") == 4         # U+201C/D は 1、U+2026 は 2
    assert x_text.weighted_length("https://thth.me/" + "x" * 100) == 23
    assert x_text.weighted_length("詳しくは thth.me で") == 2 * 4 + 1 + 23 + 1 + 2
    assert x_text.weighted_length("👨‍👩‍👧‍👦") == 2      # ZWJ の並び
    assert x_text.weighted_length("👍🏽") == 2          # 肌色
    assert x_text.weighted_length("❤️") == 2           # 異体字セレクタ
    assert x_text.weighted_length("🇯🇵") == 2          # 国旗
    assert x_text.weighted_length("©") == 1            # 異体字セレクタの無い © は字
    assert x_text.weighted_length("é") == 1       # NFC で é 1 字


def test_xの数え方は重み付き_上限は280():
    assert queuefile.count_for("x", OVER) == 284 and len(OVER) == 160
    assert queuefile.count_for("x", EDGE) == 280
    assert queuefile.limit_for("x") == x_adapter.MAX_WEIGHTED_LENGTH == 280
    assert queuefile.length_line("x", OVER) == "文字数: 284/280（x・X の重み付き）"
    assert queuefile.length_figure("x", 284, 280) == "284/280（X の重み付き）"


def test_ほかの媒体の数え方は変えない():
    text = "あ" * 10 + "👨‍👩‍👧"
    assert queuefile.count_for("threads", text) == queuefile.char_count(text)
    assert queuefile.count_for("mastodon", text) == queuefile.char_count(text)
    assert queuefile.count_for("bluesky", text) == 11
    for media in ("threads", "mastodon", "bluesky"):
        assert queuefile.length_figure(media, 501, 500) is None


# ---------------------------------------------------------------- 止める所

def _x_account(factory):
    return factory("demo-x", media="x", handle="demo")


def test_lintは超えた本文を止める(tmp_path, thth_root, isolated_account_factory):
    account = _x_account(isolated_account_factory)
    path = tmp_path / "a.md"
    path.write_text(make_queue_text(fm_overrides={"account": account["name"]},
                                    body=f"## x\n\n{OVER}\n"), encoding="utf-8")
    errors = lint_mod.lint_file(str(path))
    assert "length: x は上限を超えています: 284/280（X の重み付き）" in errors, errors
    assert not lint_mod.is_warning("length: x は上限を超えています: 284/280（X の重み付き）")


def test_lintはちょうど280を通し_暫定の注記を出さない(tmp_path, thth_root,
                                                       isolated_account_factory):
    account = _x_account(isolated_account_factory)
    path = tmp_path / "a.md"
    path.write_text(make_queue_text(fm_overrides={"account": account["name"]},
                                    body=f"## x\n\n{EDGE}\n"), encoding="utf-8")
    errors = lint_mod.lint_file(str(path))
    assert not [e for e in errors if e.startswith("length:")], errors
    assert not [e for e in errors if "x_length_weighting_unverified" in e], errors


def test_approveは超えた本文を止める(tmp_path, thth_root, isolated_account_factory):
    account = _x_account(isolated_account_factory)
    path = tmp_path / "a.md"
    path.write_text(make_queue_text(fm_overrides={"account": account["name"]},
                                    body=f"## x\n\n{OVER}\n"), encoding="utf-8")
    prepared, refusal = cli._prepare_one(str(path))
    assert prepared is None
    assert "284/280（X の重み付き）" in refusal, refusal


class _FakeAdapter:
    @staticmethod
    def capabilities():
        return set()

    def publish(self, post, *, dry_run, on_container_created=None, before_publish=None):
        raise AssertionError("長さの門より先に媒体を叩いた")


def test_sendのdry_runは超えた本文を止めてdigestを出さない(tmp_path, thth_root,
                                                        isolated_account_factory):
    account = _x_account(isolated_account_factory)
    lines = []
    result = core.send_once(account["name"], text=OVER, production_flag=False,
                            adapter_factory=lambda cfg, token: _FakeAdapter(),
                            log=lines.append, now=NOW)
    assert result.exit_code == 1
    assert "長すぎます: 284/280（X の重み付き）" in (result.message or ""), result.message
    assert not [line for line in lines if str(line).startswith("digest:")], lines


def test_sendのdry_runはちょうど280を通して重み付きで見せる(tmp_path, thth_root,
                                                          isolated_account_factory):
    account = _x_account(isolated_account_factory)
    lines = []
    result = core.send_once(account["name"], text=EDGE, production_flag=False,
                            adapter_factory=lambda cfg, token: _FakeAdapter(),
                            log=lines.append, now=NOW)
    assert "長すぎます" not in (result.message or ""), result.message
    assert "文字数: 280/280（x・X の重み付き）" in lines, lines


def test_スレッドは段ごとに重み付きで止める():
    text = ("---\nthth: 2\naccount: demo-x\npublish_at: 2026-10-15T19:00:00+09:00\n"
            "continue_until: 2026-10-15T20:00:00+09:00\nstatus: draft\nposts:\n"
            "  - index: 1\n  - index: 2\n---\n## x\n\n一段目です。\n\n"
            f"<!-- thth: 2/2 -->\n\n{OVER}\n")
    b = bundle.parse_text(text, "b.md")
    errors = bundle.check(b, account_cfg={"media": "x", "hashtags": False})
    assert "length: 2 段目が上限を超えています: 284/280（X の重み付き）" in errors, errors


def test_selectは超えた本文を落とす(tmp_path):
    publish_at = "2026-10-15T08:00:00+09:00"
    body = f"## x\n\n{OVER}\n"
    section = queuefile.extract_section(body, "x")
    sha = approval.compute_approved_sha(section=section, account="demo-x", reply_to=None,
                                        topic=None, publish_at=publish_at)
    path = tmp_path / "a.md"
    path.write_text(make_queue_text(
        fm_overrides={"account": "demo-x", "status": "approved", "approved_by": "masaru",
                      "approved_sha": sha, "publish_at": publish_at},
        body=body), encoding="utf-8")
    from tests.conftest import parse_verified
    result = select_mod.select_one(
        [parse_verified(str(path))], account_name="demo-x",
        account_cfg={"media": "x", "hashtags": False, "quiet_hours": None,
                     "min_interval_hours": 0, "stale_days": 7},
        now=NOW, last_post_at=None, recent_texts=set())
    assert result.chosen is None
    assert any(r.reason == "too_long(284)" for r in result.rejections), result.rejections
