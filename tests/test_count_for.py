"""文字数の**数え方も媒体ごと**（`queuefile.count_for()`・引継ぎ 2026-09-15 §3-D）。

`queuefile.limit_for()` は媒体ごとの**上限**を答えていたのに、数える側は 3 か所
（`send`＝`core.send_once`・`select`・`lint`）とも `queuefile.char_count()`
——**Threads の数え方**（絵文字は UTF-8 バイト数）——を直に呼んでいた。

Bluesky の上限 300 は **grapheme cluster** の数（**L2**: `app.bsky.feed.post.text`
は maxGraphemes 300）で、アダプタには既にその近似（`adapters/bluesky.py` の
`count()`）があるのに、**上限だけ Bluesky・数え方は Threads**で測っていた。
絵文字の多い本文が、Bluesky では収まるのに「長すぎます」で断られていた。

ここで固定するもの:
  - `count_for(media, text)` は媒体ごとの数え方を返す（Threads の数え方は**変えない**）
  - **同じ本文で**「Threads の数え方だと超えるが grapheme では収まる」例が、
    `select`・`lint`・`send` の 3 つとも**通る**
  - 知らない媒体は既定の数え方（`limit_for()` が `DEFAULT_MEDIA_LIMIT` を返すのと揃える）
"""
from __future__ import annotations

import datetime

from tests.conftest import make_queue_text, parse_verified
from thth import approval, core, lint as lint_mod, queuefile, select as select_mod

NOW = datetime.datetime.fromisoformat("2026-09-09T10:00:00+09:00")

# 👨‍👩‍👧 は ZWJ でつないだ 1 grapheme。UTF-8 では 18 バイト（Threads の数え方では
# 絵文字をバイト数で数えるので 1 個 = 14〜18 字ぶんに膨らむ）。
FAMILY = "👨‍👩‍👧"
# **同じ本文**が、Threads の数え方では 300 を大きく超え、grapheme では 80。
BODY = FAMILY * 80


def test_数え方は媒体ごと():
    threads_n = queuefile.count_for("threads", BODY)
    bluesky_n = queuefile.count_for("bluesky", BODY)
    assert threads_n == queuefile.char_count(BODY), "Threads の数え方は変えない"
    assert bluesky_n == 80, "grapheme の近似（1 家族 = 1）"
    # **この例が意味を持つ条件**: 片方だけが上限を超える。
    assert threads_n > queuefile.limit_for("bluesky")
    assert bluesky_n <= queuefile.limit_for("bluesky")


def test_知らない媒体は既定の数え方():
    """`limit_for()` が `DEFAULT_MEDIA_LIMIT` を返すのと揃える（読む口を止めない）。"""
    assert queuefile.count_for("まだ無い媒体", BODY) == queuefile.char_count(BODY)
    assert queuefile.count_for(None, BODY) == queuefile.char_count(BODY)


def test_mastodonとthreadsは同じ数え方のまま():
    """**既存の数え方を変えない**（変えるとこれまでの判定が静かにずれる）。"""
    for media in ("threads", "mastodon"):
        assert queuefile.count_for(media, BODY) == queuefile.char_count(BODY)


# ---------------------------------------------------------------- select

def _bluesky_cfg(**overrides):
    cfg = {"media": "bluesky", "hashtags": False, "quiet_hours": None,
           "min_interval_hours": 0, "stale_days": 7}
    cfg.update(overrides)
    return cfg


PUBLISH_AT = "2026-09-09T08:00:00+09:00"


def _write(tmp_path, name, body):
    """承認まで済んだ queue ファイル（`approved_sha` も本物を書く）。"""
    path = tmp_path / name
    section = queuefile.extract_section(body, "bluesky")
    sha = approval.compute_approved_sha(
        section=section, account="demo-bluesky", reply_to=None, topic=None,
        publish_at=PUBLISH_AT)
    path.write_text(make_queue_text(
        fm_overrides={"account": "demo-bluesky", "status": "approved",
                      "approved_by": "masaru", "approved_sha": sha,
                      "publish_at": PUBLISH_AT},
        body=body), encoding="utf-8")
    return str(path)


def test_selectはblueskyをgraphemeで数える(tmp_path):
    """Threads の数え方なら `too_long`、grapheme なら通る本文。"""
    path = _write(tmp_path, "a.md", f"## bluesky\n\n{BODY}\n")
    result = select_mod.select_one(
        [parse_verified(path)], account_name="demo-bluesky",
        account_cfg=_bluesky_cfg(), now=NOW, last_post_at=None, recent_texts=set())
    assert not [r for r in result.rejections if r.reason.startswith("too_long")], \
        result.rejections
    assert result.chosen is not None and result.chosen.path == path


def test_selectはblueskyでも本当に長い本文は落とす(tmp_path):
    """**上限そのものは緩めない**（grapheme で 301）。"""
    path = _write(tmp_path, "a.md", "## bluesky\n\n" + ("あ" * 301) + "\n")
    result = select_mod.select_one(
        [parse_verified(path)], account_name="demo-bluesky",
        account_cfg=_bluesky_cfg(), now=NOW, last_post_at=None, recent_texts=set())
    assert result.chosen is None
    assert any(r.reason == "too_long(301)" for r in result.rejections)


# ---------------------------------------------------------------- lint

def test_lintはblueskyをgraphemeで数える(tmp_path, thth_root, isolated_account_factory):
    account = isolated_account_factory("demo-bluesky", media="bluesky",
                                        handle="demo.bsky.social")
    path = tmp_path / "a.md"
    path.write_text(make_queue_text(
        fm_overrides={"account": account["name"]},
        body=f"## bluesky\n\n{BODY}\n"), encoding="utf-8")
    errors = lint_mod.lint_file(str(path))
    assert not [e for e in errors if e.startswith("length:")], errors


def test_lintはblueskyでも本当に長い本文を断る(tmp_path, thth_root,
                                                isolated_account_factory):
    account = isolated_account_factory("demo-bluesky", media="bluesky",
                                        handle="demo.bsky.social")
    path = tmp_path / "a.md"
    path.write_text(make_queue_text(
        fm_overrides={"account": account["name"]},
        body="## bluesky\n\n" + ("あ" * 301) + "\n"), encoding="utf-8")
    errors = lint_mod.lint_file(str(path))
    assert "length: bluesky は 300 字以内（301 字）" in errors, errors


# ---------------------------------------------------------------- send

class _FakeAdapter:
    """dry-run の `send` はここまで来ない（文字数の門はもっと手前）。"""

    @staticmethod
    def capabilities():
        return set()

    def publish(self, post, *, dry_run, on_container_created=None, before_publish=None):
        raise AssertionError("dry-run で媒体を叩いた")


def test_sendはblueskyをgraphemeで数える(tmp_path, thth_root, isolated_account_factory):
    account = isolated_account_factory("demo-bluesky", media="bluesky",
                                        handle="demo.bsky.social")
    result = core.send_once(account["name"], text=BODY, production_flag=False,
                             adapter_factory=lambda cfg, token: _FakeAdapter(),
                             log=lambda _line: None, now=NOW)
    assert "長すぎます" not in (result.message or ""), result.message


def test_sendはblueskyでも本当に長い本文は断る(tmp_path, thth_root,
                                                isolated_account_factory):
    account = isolated_account_factory("demo-bluesky", media="bluesky",
                                        handle="demo.bsky.social")
    result = core.send_once(account["name"], text="あ" * 301, production_flag=False,
                             adapter_factory=lambda cfg, token: _FakeAdapter(),
                             log=lambda _line: None, now=NOW)
    assert "長すぎます（301 字・上限 300 字）" in (result.message or ""), result.message
