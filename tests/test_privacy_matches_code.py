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

from thth import map_store, map_world, meter, relay

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
    ]


@pytest.mark.parametrize("what,phrase", facts(), ids=[f[0] for f in facts()])
def test_privacyの数はコードの定数と同じ(what, phrase):
    page = PRIVACY.read_text(encoding="utf-8")
    assert phrase in page, f"privacy に「{phrase}」が無い（{what}）。コードと privacy（tools/build_site.py）のどちらかを直す"


def test_PBKDF2の回数はWorkerとththで同じ():
    assert js_const("person.js", "ITERATIONS") == relay.ITERATIONS
