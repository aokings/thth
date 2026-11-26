"""断りの目録の項目（括り＝THTH アカウントの器・`thth admin ws …`・`thth/ws_admin.py`・`accounts.workspace_dir`）。
thth/refusal_catalog.py が読む。

設計: docs/設計_THTHアカウント（括り）_叩き台_2026-11-26.md の段 1。原則 `docs/原則_前向きな断り.md`。
`since` は今の版（`thth/VERSION` を越えない柵）。版上げのとき、次の版に直す。
"""
from __future__ import annotations

_C = ("cli",)
_SINCE = "5.31.0"
_LIST = "thth admin ws list"


def _entry(why, fix, command, better):
    return {"kind": "refusal", "why": why, "fix": fix, "command": command, "better": better, "doors": _C,
            "since": _SINCE}


ENTRIES = {
    "workspace_name_invalid": _entry(
        "括りの名の形が違います。何も書いていません。",
        "括りの名は小文字で始まる英小文字・数字・`-`（32 字まで）にしてください。",
        _LIST,
        "媒体群の呼び名に近い短い名にすると、一覧と置き場の名前が読みやすくなります。"),
    "workspace_unknown": _entry(
        "台帳にその名の括りがありません。",
        "`thth admin ws list` で括りの名を確かめてください。",
        _LIST,
        "括りを作るときは `thth admin ws add <括り> --lead <媒体>` で、台帳と置き場が一緒にできます。"),
    "workspace_exists": _entry(
        "同じ名の括りがもうあります。何も書いていません。",
        "`thth admin ws show <括り>` で中を見てください。別の括りなら別の名にしてください。",
        "thth admin ws show <括り>",
        "媒体を足すだけなら `thth admin ws media add <括り> <媒体>` で済みます。"),
    "workspace_folder_exists": _entry(
        "台帳に無いのに、括りのフォルダ `ws/<括り>/` が残っています。何も書いていません。",
        "フォルダの中を確かめ、要らなければ名前を替えて退けてから足し直してください。",
        _LIST,
        "括りは `thth admin ws add` でだけ作ると、台帳とフォルダが食い違いません。"),
    "workspace_unavailable": _entry(
        "その括りのフォルダがありません（作られていないか、symlink になっています）。",
        "`thth admin ws list` で、フォルダのある括りか確かめてください。",
        _LIST,
        "括りの名は鍵か `THTH_WS` で決まります。打つ人が名指しするのは管理者だけにすると取り違えません。"),
    "medium_name_invalid": _entry(
        "媒体の名前の形が違います。何も書いていません。",
        "媒体の名前は repo の名前と同じ綴り（英小文字・数字・`-`）にしてください。",
        _LIST,
        "hub の project と h2d の owner と同じ綴りにすると、3 つの部品が同じ括りを引けます。"),
    "medium_in_other_workspace": _entry(
        "その媒体は、ほかの括りに結ばれています。台帳の書き換えでは括りを移しません（置き場のデータが付いてこないため）。",
        "括りを移すのは、置き場ごと移す管理者の段取りで行ってください。",
        _LIST,
        "新しい媒体群は新しい媒体の名前（新しい repo）で始めると、移す作業が要りません。"),
    "workspaces_map_invalid": _entry(
        "括りの台帳（workspaces.json）が読めないか、形が違います。新しく書く操作はしていません。",
        "`tools/workspaces_check.py` で台帳の形を確かめ、誤りの行を直してください。",
        _LIST,
        "台帳は手で直さず `thth admin ws …` だけで書くと、途中の姿が読まれません。"),
}
