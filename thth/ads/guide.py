"""広告の手順の skill（`thth-ads`）の在り処と、LLM に返す「次の一手」（5.6.3・masaru 11/03）。

**読めとかやれとか言う前に skill を示す**（masaru 11/03）: 広告の命令が LLM に返す次の一手（`next_steps`）と
エラーの文は、先頭に skill `thth-ads` の在り処を置き、そのあとに個別の一手を書く。

skill の置き場は repo の `skills/thth-ads/SKILL.md`。wheel には `thth/skills/thth-ads/SKILL.md` として入る
（pyproject の force-include `"skills" = "thth/skills"`）。`thth ads skill` が場所を、`--print` が全文を出す。

ピクセルは**読むだけ**（`/act_<id>/adspixels`）。作る・設定を変える口は THTH に無い。サイトのコードにも触らない。
ピクセルの基本のコードの入れ方の公式の文書: developers.facebook.com/docs/meta-pixel/get-started
（イベントマネージャで基本のコードを取り、サイトの全部の頁の `<head>` に入れる・文書）。
"""
from __future__ import annotations

import os

SKILL_NAME = "thth-ads"
PIXEL_DOC_URL = "https://developers.facebook.com/docs/meta-pixel/get-started"
_HERE = os.path.dirname(os.path.abspath(__file__))
# 探す順: wheel（thth/skills/…）→ repo（<repo>/skills/…）。
_CANDIDATES = (os.path.join(_HERE, os.pardir, "skills", SKILL_NAME, "SKILL.md"),
               os.path.join(_HERE, os.pardir, os.pardir, "skills", SKILL_NAME, "SKILL.md"))


def skill_path() -> str | None:
    for path in _CANDIDATES:
        if os.path.isfile(path):
            return os.path.normpath(path)
    return None


def skill_line() -> str:
    """人と LLM に見せる 1 行（**広告の文の先頭に置く**）。"""
    path = skill_path()
    return (f"広告の手順は skill {SKILL_NAME}（`thth ads skill` で場所、`thth ads skill --print` で全文"
            + (f"・{path}" if path else "") + "）")


def skill_ref() -> dict:
    """json に載せる skill の欄（名前と入手先）。"""
    return {"name": SKILL_NAME, "path": skill_path(), "command": "thth ads skill --print",
            "text": skill_line()}


def skill_step() -> dict:
    return {"kind": "skill", "skill": SKILL_NAME, "path": skill_path(), "text": skill_line()}


def pixel_steps(name: str, pixels, *, landing_url: str | None = None, error: str | None = None) -> list:
    """サイトへの誘導で頁が開いた数を測るための次の一手。**先頭は skill**。測れる状態なら skill だけ。

    `pixels` は `Reader.pixels()` の並び（読めなければ None と `error`）。
    """
    where = landing_url or "<誘導先の URL>"
    steps = [skill_step()]
    if pixels is None:
        steps.append({"kind": "pixel_unreadable", "error": error,
                      "text": f"ピクセルを読めませんでした（{error}）。thth ads doctor {name} で見る"})
        return steps
    usable = [p for p in pixels if not p.get("is_unavailable")]
    if not usable:
        steps.append({"kind": "pixel_create", "doc": PIXEL_DOC_URL,
                      "text": (f"広告アカウントにピクセルがありません。イベントマネージャでピクセルを作る（THTH からは"
                               f"作らない・{PIXEL_DOC_URL}）。作ったら thth ads doctor {name} で見る")})
        return steps
    fired = [p for p in usable if p.get("last_fired_time")]
    pixel = (fired or usable)[0]
    if not fired:
        steps.append({"kind": "pixel_install", "pixel_id": pixel["id"], "landing_url": landing_url,
                      "doc": PIXEL_DOC_URL,
                      "text": (f"誘導先 {where} のサイトにピクセル {pixel['id']} の基本のコードを入れる"
                               f"（{PIXEL_DOC_URL}）。入れたら thth ads doctor {name} で動いたかを見る")})
    else:
        steps.append({"kind": "pixel_check_page", "pixel_id": pixel["id"], "landing_url": landing_url,
                      "last_fired_time": pixel["last_fired_time"], "doc": PIXEL_DOC_URL,
                      "text": (f"ピクセル {pixel['id']} は {pixel['last_fired_time']} に動いています。誘導先 {where} の"
                               f"頁にも基本のコードが入っているかを確かめる（{PIXEL_DOC_URL}）。入れたら "
                               f"thth ads doctor {name} で動いたかを見る")})
    if landing_url is None:
        steps.append({"kind": "landing_url_unknown",
                      "text": (f"誘導先の URL が分かりません。boost の --landing-url か台帳の landing_url に書くと、"
                               f"次の一手に URL が入ります")})
    return steps


def pixel_ready(pixels) -> bool | None:
    """ピクセルが動いた記録があるか（読めなければ None）。"""
    if pixels is None:
        return None
    return any(p.get("last_fired_time") and not p.get("is_unavailable") for p in pixels)
