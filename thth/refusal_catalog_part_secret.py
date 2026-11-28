"""断りの目録の項目（鍵の入口: `thth token set`・台帳の照合・`thth secret accept` の中の入口）。thth/refusal_catalog.py が読む。

5.32.0 の監査の軽い 11（5.32.1）: 鍵の入口の断りは「符丁: 文」の素の文で、目録の柵が掛かっていなかった。ここに
載せて、なぜ・通すには・もっと良く の 3 つを持たせる。文は人が手で打つときにも、monban の家の係から呼ばれるときにも
読めるように書く（家の係は終了コードだけを見る）。
原則 `docs/原則_前向きな断り.md`。since は版上げのときに直す（今の版を越えない柵があるので、今は今の版を置く）。
"""
from __future__ import annotations

_C = ("cli",)
_SINCE = "5.32.0"
_TOKEN_SET = "thth token set <口座> --by <名前>"
_REVOKE = "thth token revoke <口座> --by <名前>"
_HATENA = "thth principal auth <主体> --begin --by <名前>"


def _entry(why, fix, command, better, kind="refusal"):
    return {"kind": kind, "why": why, "fix": fix, "command": command, "better": better, "doors": _C,
            "since": _SINCE}


ENTRIES = {
    # ---------------------------------------------------------------- 口座の token（thth/oauth.py）
    "token_handle_missing": _entry(
        "台帳に handle が無いので、入れたトークンが正しい口座のものか照合できず、保存しませんでした。",
        "thth account set <口座> handle <handle> --by <名前> で台帳に handle を書いてから入れ直してください"
        "（今の鍵があるときは、先に thth token revoke <口座> --by <名前> で外します）。",
        "thth account set <口座> handle <handle> --by <名前>",
        "口座を作るとき（thth account add --handle）に handle を決めておくと、鍵を入れる前に直す手間が要りません。"),
    "token_account_mismatch": _entry(
        "入れたトークンが指す口座（handle か user_id）が、台帳か今の鍵の口座と違うので、保存しませんでした"
        "（別の口座の鍵を入れると、その口座の queue が別の人から出ます）。",
        "その口座でトークンを発行し直して入れてください。台帳の方が違っていたなら、今の鍵を thth token revoke で外し、"
        "thth account set <口座> handle または user_id で直してから入れ直してください。",
        _TOKEN_SET,
        "台帳に user_id まで書いておくと、handle を付け替えた別の人の鍵も番号で止められます（置いたとき空なら THTH が書きます）。"),
    "token_unusable": _entry(
        "入れたトークンで口座の確認（媒体の me）が通らなかったか、応答に id か username が無かったので、保存しませんでした。",
        "期限と権限を管理画面で確かめ、発行し直したトークンを入れてください。",
        _TOKEN_SET,
        "長期のトークンを発行してすぐ入れると、期限切れで断られる回り道が減ります。"),
    "token_set_wrong_media": _entry(
        "この媒体の鍵は貼り付けでは入りません（Bluesky の App Password などは別の入口です）。",
        "thth auth <口座> --by <名前> で入れてください。",
        "thth auth <口座> --by <名前>",
        "媒体ごとの入口は thth secret kinds の表にまとまっているので、先に見ておくと迷わず入れられます。"),
    "token_set_ledger_unavailable": _entry(
        "鍵を置いたあと台帳に user_id を書こうとしましたが、台帳が読めない形だったので、鍵ごと元に戻しました。",
        "thth account <口座> で台帳が読めるかを確かめ、台帳の JSON を直してから入れ直してください。",
        "thth account <口座>",
        "台帳は thth account set で直すと、形を崩さずに書き換えられます。",
        kind="cannot_say"),
    # ---------------------------------------------------------------- 店（thth/shop/cli.py）
    "store_token_unreadable": _entry(
        "この店の今の鍵のファイルはあるのに読めず（壊れた JSON・店の種類と違う形・店の番号の無い形）、写しも無いので、"
        "新しい鍵が前と同じ店のものか照合できません。人が見ていない入力口からは置き換えずに止めました（今のファイルはそのままです）。",
        "店の持ち主が端末から thth shop token set <店> --by <名前> で置き直してください（BASE は --expires、Shopify は "
        "--auth も付けます）。",
        "thth shop token set <店> --by <名前>",
        "鍵のファイルは手で書き換えず thth shop token set だけで置き、一度 thth shop diff で写しを取っておくと、"
        "照合の元（店の番号）が崩れません。"),
    # ---------------------------------------------------------------- 台帳の user_id（thth/account_settings.py）
    "invalid_user_id": _entry(
        "user_id に使えない字があります（英数字と :・.・_・- だけ、128 字まで）。",
        "媒体の me が返す番号（Bluesky は did）をそのまま入れてください。外すなら - を入れます。",
        "thth account set <口座> user_id <番号> --by <名前>",
        "番号は thth token set が置いたときに自分で書くので、手で入れるのは直すときだけで足ります。"),
    "user_id_locked_by_token": _entry(
        "トークンのある口座なので、user_id を変えません（取り違え防止の照合の片側です）。",
        "変えるなら、thth token revoke <口座> --by <名前> でトークンを外してから user_id を直し、入れ直してください。",
        _REVOKE,
        "口座を替えるときは、外す・直す・入れ直すの順で打つと、照合が新しい口座で働きます。"),
    # ---------------------------------------------------------------- Bluesky（thth/authpassword.py）
    "bluesky_account_mismatch": _entry(
        "App Password で入った口座（did か handle）が、台帳の口座と違うので、保存しませんでした。",
        "台帳の handle の口座で App Password を作り直して入れてください。",
        "thth auth <口座> --by <名前>",
        "App Password に口座の名前を付けて作っておくと、どの口座のものかを取り違えずに入れられます。"),
    "bluesky_session_failed": _entry(
        "App Password で Bluesky にログインできなかったので、保存しませんでした。",
        "Bluesky の設定で App Password を作り直し、台帳の handle の口座で入れてください。",
        "thth auth <口座> --by <名前>",
        "使わなくなった App Password を Bluesky の設定で消しておくと、どれが今のものかが分かりやすくなります。"),
    # ---------------------------------------------------------------- Facebook（thth/adapters/facebook.py）
    "facebook_page_mismatch": _entry(
        "入れた鍵のページが、台帳の page_id のページと違うので、保存しませんでした。",
        "台帳の page_id のページで鍵を発行し直して入れてください。",
        _TOKEN_SET,
        "ページの鍵はページを選んでから発行すると、別のページの鍵を取り違えずに済みます。"),
    # ---------------------------------------------------------------- はてな（thth/principal_cli.py）
    "hatena_id_mismatch": _entry(
        "認可したはてな ID が、主体の台帳のはてな ID と違うので、token を書かずに預けも捨てました。",
        "主体のはてな ID でログインし直してから、--begin からやり直してください。",
        _HATENA,
        "はてなに主体の ID でログインしたブラウザで認可の URL を開くと、別の ID で認可せずに済みます。"),
    "initiate_failed": _entry(
        "はてなに request token を求めましたが、受けてもらえませんでした（consumer の値が違うか、はてな側が落ちている）。",
        "はてなのアプリの設定で consumer key と secret を確かめ、入れ直してから --begin を打ってください。",
        _HATENA,
        "consumer を入れたら続けて --begin を打つと、値の違いにすぐ気づけます。"),
    "token_failed": _entry(
        "確認の文字列で access token を求めましたが、はてなに受けてもらえませんでした（文字列の違い・期限切れ）。",
        "--begin からやり直し、出た URL で認可して新しい確認の文字列を入れてください。",
        _HATENA,
        "確認の文字列は出てから 30 分のうちに入れると、預けの期限に掛かりません。"),
    "verifier_invalid": _entry(
        "確認の文字列の形が違います（空・空白や改行を含む）。",
        "認可の画面に出た確認の文字列を、そのまま 1 行で入れてください。",
        _HATENA,
        "画面の文字列をコピーして貼ると、打ち間違いが起きません。"),
    "verifier_pending_missing": _entry(
        "先に --begin で預けた request token が無いか、期限（30 分）を過ぎたので、確認の文字列を受けられません。",
        "--begin を打ち直し、出た URL で認可してから確認の文字列を入れてください。",
        _HATENA,
        "--begin と確認の文字列は続けて打つと、預けの期限に掛かりません。"),
    "token_present": _entry(
        "主体には既に token があり、--force が無いので、入れ替えずに止めました（今の token はそのままです）。",
        "入れ替えるなら --force を付けて打ち直してください。消すなら --forget を使います。",
        "thth principal auth <主体> --force --by <名前>",
        "期限の近い token だけを入れ替えるようにすると、動いている token を誤って替えずに済みます。"),
    "principal_token_busy": _entry(
        "同じ主体の鍵を、別の打ち込みが書き換えている最中なので、重ねて書かずに止めました。",
        "少し待ってから、もう一度打ってください。",
        None,
        "同じ主体の鍵は 1 人ずつ入れると、重なって待つことがありません。"),
}
