"""privacy とコードの一致の柵（設計「監査と審査の機械化と前向きな断り」§6 の (A)・段 D）。

`callback/public/privacy/index.html` が言う数（保持・期限・回数・上限）を、その数を決めているコードの定数から
作った文と突き合わせる。コードの数を変えて privacy を直さないと赤になる（逆も同じ）。

既にある privacy の試験（`tests/test_site.py`・`tests/test_v212_relay.py`・`tests/test_v3180_where_keep.py`）は
文の有無を見るが、文の中の数をコードの定数から引かない（字面の写し）。ここはその「数の出所」を結ぶ最小の形。
privacy の文は `tools/build_site.py` から生成する（`test_site.py` が一致を見る）。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from thth import map_store, map_world, meta_public, meta_write, meter, permission_watch, relay
from thth.meta import calls as meta_calls

ROOT = Path(__file__).resolve().parent.parent
PRIVACY = ROOT / "callback" / "public" / "privacy" / "index.html"
SRC = ROOT / "callback" / "src"


def js_const(file: str, name: str) -> int:
    """`callback/src/<file>` の `NAME=1_000` の形の数の定数（`export const A=…, B=…` の並びも読む）。"""
    text = (SRC / file).read_text(encoding="utf-8")
    found = re.search(rf"\b{name}\s*=\s*([\d_]+)\b", text)
    assert found, f"{file} に {name} が無い"
    return int(found.group(1).replace("_", ""))


def seconds(ms: int) -> int:
    assert ms % 1000 == 0
    return ms // 1000


def minutes(ms: int) -> int:
    assert ms % 60_000 == 0
    return ms // 60_000


def hours_en(ms: int) -> str:
    assert ms % 3_600_000 == 0
    n = ms // 3_600_000
    return "one hour" if n == 1 else f"{n} hours"


# （何の数か, コードの値から作った privacy の文の断片）。
def facts() -> list[tuple[str, str]]:
    return [
        ("relay の認可コード（relay-object.js CODE_MS）",
         f"at most {seconds(js_const('relay-object.js', 'CODE_MS'))} seconds after receipt"),
        ("relay の認可コード・日本語", f"{seconds(js_const('relay-object.js', 'CODE_MS'))}秒"),
        ("relay の session（relay-object.js STATE_MS）",
         f"{seconds(js_const('relay-object.js', 'STATE_MS'))}-second session lifetime"),
        ("PBKDF2 の回数（callback person.js ITERATIONS）",
         f"{js_const('person.js', 'ITERATIONS'):,} iterations"),
        ("PBKDF2 の回数（thth/relay.py ITERATIONS）", f"{relay.ITERATIONS:,} iterations"),
        ("/activity の要約と頼まれた操作（person-object.js ACTIVITY_TTL）",
         f"Activity summaries and requested actions on thth.me: at most "
         f"{hours_en(js_const('person-object.js', 'ACTIVITY_TTL'))}"),
        ("頼まれた操作（person-object.js ACTION_TTL）",
         f"Activity summaries and requested actions on thth.me: at most "
         f"{hours_en(js_const('person-object.js', 'ACTION_TTL'))}"),
        ("api/v1 の頼み（person-object.js API_REQUEST_MS）",
         f"Command requests on thth.me/api/v1: at most {seconds(js_const('person-object.js', 'API_REQUEST_MS'))} seconds"),
        ("api/v1 の結果（person-object.js API_RESULT_MS）",
         f"their results: at most {minutes(js_const('person-object.js', 'API_RESULT_MS'))} minutes"),
        ("/activity のサインイン（login-object.js LOGIN_TTL）",
         f"Sign-in to the activity page: {minutes(js_const('login-object.js', 'LOGIN_TTL'))} minutes"),
        ("アップロードの capability URL（media.js MEDIA_TTL）",
         f"{minutes(js_const('media.js', 'MEDIA_TTL'))} minutes for uploads"),
        ("R2 の置き場の消し（media.js RAW_RETENTION）",
         f"{js_const('media.js', 'RAW_RETENTION') // 3_600_000} hours after it was created"),
        ("トピックマップの語の数（map_store.MAX_NODES）", f"at most {map_store.MAX_NODES} per project"),
        ("トピックマップの語の数・日本語", f"最大 {map_store.MAX_NODES} 語"),
        ("トピックマップの保持（map_store.MAX_RETENTION_DAYS）", f"for at most {map_store.MAX_RETENTION_DAYS} days"),
        ("トピックマップの保持・日本語", f"最大 {map_store.MAX_RETENTION_DAYS} 日保存します"),
        ("少ない数は保存しない（map_world.SMALL_SAMPLE）", f"A number below {map_world.SMALL_SAMPLE}"),
        ("少ない数は保存しない・日本語", f"{map_world.SMALL_SAMPLE} 未満の数"),
        ("Meta の通知の保持（webhook-object.js RETENTION・7 * 24 … の頭の日数）",
         f"at most {js_const('webhook-object.js', 'RETENTION')} days"),
        ("Meta の通知の保持・日本語", f"最長 {js_const('webhook-object.js', 'RETENTION')} 日"),
        ("命令の控えの保持（meter.KEEP_DAYS）", f"sent nowhere): {meter.KEEP_DAYS} days"),
        ("命令の控えの保持・日本語", f"外へ送りません）: {meter.KEEP_DAYS}日"),
        # 計画 Meta 全部盛り M6: M3・M4・M5 の数（DM の窓・ハッシュタグの柵・広告の呼び出しの数・権限の不使用）。
        ("DM の窓（meta_write.WINDOW）",
         f"within {_hours(meta_write.WINDOW)} hours of the other person's last message"),
        ("DM の窓・日本語", f"相手の最後の通から {_hours(meta_write.WINDOW)} 時間の内"),
        ("人の担当者の窓（meta_write.HUMAN_AGENT_WINDOW）",
         f"human_agent tag, within {meta_write.HUMAN_AGENT_WINDOW.days} days of it"),
        ("人の担当者の窓・日本語", f"人の担当者が {meta_write.HUMAN_AGENT_WINDOW.days} 日の内に"),
        ("非公開の返事の窓（meta_write.PRIVATE_REPLY_WINDOW）",
         f"is sent within {meta_write.PRIVATE_REPLY_WINDOW.days} days of the comment"),
        ("非公開の返事の窓・日本語", f"コメントから {meta_write.PRIVATE_REPLY_WINDOW.days} 日の内に"),
        ("IG のハッシュタグの柵（meta_public.HASHTAG_LIMIT・HASHTAG_WINDOW）",
         f"at most {meta_public.HASHTAG_LIMIT} distinct hashtags in {meta_public.HASHTAG_WINDOW.days} days"),
        ("IG のハッシュタグの柵・日本語", f"{meta_public.HASHTAG_WINDOW.days} 日で {meta_public.HASHTAG_LIMIT} 種"),
        ("IG のハッシュタグの控えの保持（meta_public.HASHTAG_WINDOW）",
         f"the hashtag and the first time only): {meta_public.HASHTAG_WINDOW.days} days"),
        ("IG のハッシュタグの控えの保持・日本語", f"タグと最初の時刻だけ）: {meta_public.HASHTAG_WINDOW.days} 日"),
        ("広告の呼び出しの数の保持（meta/calls.KEEP_DAYS）", f"per-day counts for the last {meta_calls.KEEP_DAYS} days"),
        ("広告の呼び出しの数の保持・日本語", f"日ごとの数を直近 {meta_calls.KEEP_DAYS} 日"),
        ("最小の読みの日（permission_watch.KEEP_DAYS）", f"has not been used for {permission_watch.KEEP_DAYS} days"),
        ("最小の読みの日・日本語", f"{permission_watch.KEEP_DAYS} 日使われていない"),
        ("権限の不使用の上限（permission_watch.LIMIT_DAYS）", f"not used for {permission_watch.LIMIT_DAYS} days"),
        ("権限の不使用の上限・日本語", f"{permission_watch.LIMIT_DAYS} 日使わなかった権限"),
        ("利用者ごとの期限（permission_watch.USER_LIMIT_DAYS）",
         f"expire {permission_watch.USER_LIMIT_DAYS} days after the user was last active"),
        ("利用者ごとの期限・日本語", f"{permission_watch.USER_LIMIT_DAYS} 日のデータアクセスの期限"),
    ]


def _hours(delta) -> int:
    assert delta.total_seconds() % 3600 == 0
    return int(delta.total_seconds() // 3600)


@pytest.mark.parametrize("what,phrase", facts(), ids=[f[0] for f in facts()])
def test_privacyの数はコードの定数と同じ(what, phrase):
    page = PRIVACY.read_text(encoding="utf-8")
    assert phrase in page, f"privacy に「{phrase}」が無い（{what}）。コードと privacy（tools/build_site.py）のどちらかを直す"


def test_PBKDF2の回数はWorkerとththで同じ():
    assert js_const("person.js", "ITERATIONS") == relay.ITERATIONS


def test_Xの項はadapterの口と合う():
    """X の adapter が自分の投稿の数・返信・反応を取れるなら、privacy は「集めない／できない」と言わない（11/09）。

    9/27 の文「Replies and metrics are not collected」が、c8511ae（5.13.0）で数を記録したあとも残っていた。
    """
    from thth.adapters.x import XAdapter

    page = PRIVACY.read_text(encoding="utf-8")
    item = re.search(r"<li>X: (.*?)</li>", page, re.S)
    assert item, "privacy に X の項が無い"
    text = item.group(1)
    if hasattr(XAdapter, "owned_metrics"):
        assert "metrics" in text and "not collected" not in text
    if hasattr(XAdapter, "conversation"):
        assert "replies to its posts" in text
    if {"repost", "like"} <= set(XAdapter.CAPABILITIES):
        assert "reposts and likes" in text
    # 検索と会話の読み（監査 5.14.0）。他人の投稿を読む口があるなら、項に書き、検索の節にも X を挙げる。
    # 片方だけ残っても縛るように、口ごとに見る（監査 5.14.0b の 6）。
    caps = set(XAdapter.CAPABILITIES)
    if "keyword_search" in caps:
        assert "search of other people's recent public posts" in text
        search = re.search(r"<strong>Keyword search\.</strong>(.*?)</p>", page, re.S)
        assert search and "Mastodon and X" in search.group(1)
        assert "Mastodon・X の検索" in page
    if "thread_read" in caps:
        assert "replies in one post's conversation" in text
    if caps & {"keyword_search", "thread_read"}:
        assert "not saved" in text


def test_WhatsAppの項は口と合い_保存しないと言う():
    """thth wa があるなら、privacy に WhatsApp の節があり、番号と本文を保存しないと日英で言う（監査 5.14.0b の 3）。
    端末や確認の段数に縛った言い方はしない（原則 2026-11-08）。"""
    import importlib.util
    if importlib.util.find_spec("thth.wa.cli") is None:
        return
    page = PRIVACY.read_text(encoding="utf-8")
    en = re.search(r"<h2>WhatsApp</h2>\s*<p>(.*?)</p>", page, re.S)
    assert en, "privacy に WhatsApp の節が無い"
    body = en.group(1)
    assert "recipient's number" in body and "does not save them" in body
    assert "terminal" not in body
    ja = re.findall(r"<h2>WhatsApp</h2>\s*<p>(.*?)</p>", page, re.S)
    assert len(ja) == 2, "WhatsApp の節が日英の 2 つでない"
    assert "相手の番号" in ja[1] and "保存しません" in ja[1] and "端末" not in ja[1]
