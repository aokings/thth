"""断りの目録の項目（店の変化の連動・`thth link …`・`thth/register/linkage.py`・`bundle.py`・`thth/link/`）。
thth/refusal_catalog.py が読む。

符丁は `thth/register/errors.py` の `REASONS`（台帳の機械と同じ `RegisterRefused`）。束の中の出し先ごとの止まりの
符丁も同じ表から引く（`thth link show` が 1 行ずつ添える）。原則 `docs/原則_前向きな断り.md`。
`since` は今の版（`thth/VERSION` を越えない柵）。版上げのとき、次の版に直す。
"""
from __future__ import annotations

_C = ("cli",)
_SINCE = "5.26.0"
_CHECK = "thth link check <台帳>"
_SHOW = "thth link show <台帳>"
_SEND = "thth link send <台帳> --bundle <束> --by <名前>"


def _entry(why, fix, command, better):
    return {"kind": "refusal", "why": why, "fix": fix, "command": command, "better": better, "doors": _C,
            "since": _SINCE}


ENTRIES = {
    # IG のストーリー（`adapters/instagram_media.py` の `intent_error`・`media_capabilities.REFUSALS`）。
    "story_options": {
        "kind": "refusal",
        "why": "ストーリーには、ほかの投稿の指定（AI で作った印など）を一緒に付けられません。",
        "fix": "post_options を story: true だけにしてから打ち直してください。",
        "command": "thth lint <ファイル>",
        "better": "フィードにも出したいときは、ストーリーとフィードを別の原稿にすると、それぞれに合う指定を付けられます。",
        "doors": ("cli", "mcp"), "since": _SINCE},
    "story_no_media": {
        "kind": "refusal",
        "why": "ストーリーは画像が 1 枚要るのに、画像がありません（字幕は付かないので、画像が中身そのものです）。何も出していません。",
        "fix": "JPEG の画像を 1 枚だけ添付してから打ち直してください。",
        "command": "thth lint <ファイル>",
        "better": "縦長（9:16）の画像に日付と時刻を大きく入れると、ストーリーの画面いっぱいで一目で伝わります。",
        "doors": ("cli", "mcp"), "since": _SINCE},
    # ダッシュボード・thth scenario の cannot_say（束の記録 `register_state/<台帳>/linkage_bundles.json` が読めない）。
    "links_unreadable": {
        "kind": "cannot_say", "why": None,
        "fix": "thth link show <台帳> で束の記録が読めるかを確かめ、読めなければ thth report file で開発のセッションへ知らせてください。",
        "command": "thth link show <台帳>",
        "better": "束の記録は THTH が書く物なので手で直さず、知らせて直すと、出した物と待ちの物の数がそろったまま保てます。",
        "doors": ("dashboard",), "since": _SINCE},
    "register_linkage_invalid": _entry(
        "台帳のフォルダの連動の宣言（.thth/linkage.json）が無いか、形が決まりに合っていません（1 行目の（）が最初の語・"
        "崩れた宣言からは何も自動で出しません）。",
        "thth link check <台帳> が 1 行ずつ出す直し方のとおりに .thth/linkage.json を直してから打ち直してください。",
        _CHECK,
        "同意は 店・出し先・きっかけ・期限・書いた人と日時 を全部書くと、その範囲だけが確認なしで出て、ほかは 1 回ずつ見られます。"),
    "register_link_args": _entry(
        "thth link send には --bundle <束> か --auto のどちらか 1 つだけが要ります。",
        "1 回ずつ出すなら --bundle に束の id、同意のある物だけ確認なしで出すなら --auto を付けて打ち直してください。",
        _SHOW,
        "thth link show で束の id と状態を見てから選ぶと、出す物を取り違えません。"),
    "register_link_bundle_missing": _entry(
        "その id の束がこの台帳にありません（打ち間違いか、別の台帳の束です）。",
        "thth link show <台帳> に出る束の id をそのまま写して打ち直してください。",
        _SHOW,
        "--json の bundles[].id を写すと、id の写し違いが起きません。"),
    "register_link_image_missing": _entry(
        "IG のフィードとストーリーは画像が要るのに、使える画像がありません（行の頭の image も宣言の既定の image も無い・"
        "ファイルが無い・JPEG でない・8 MB を超える のどれか。（）に語があります）。何も出していません。",
        "その行の頭に image: 画像/<名>.jpg（台帳のフォルダの中の JPEG）を足すか、.thth/linkage.json の出し先に既定の "
        "image を書いてから、thth link make で束を作り直してください。",
        _CHECK,
        "ストーリーは縦長（9:16）の画像にすると画面いっぱいに出て、日付と時刻を画像の中に大きく入れると一目で伝わります。"),
    "register_link_too_long": _entry(
        "臨時の休みや時間の変わりが 14 日を超えるので、Google マップに 1 日ずつ書く形にしていません。",
        "行を 14 日までに分けるか、長い休みは Google マップの画面で営業の状態（臨時休業）を変えてください。",
        _SHOW,
        "長い休みは台帳の行を 2 つに分けると、見直し日も分けて持てて、マップにも 1 日ずつ正しく出ます。"),
    "register_link_google_changed": _entry(
        "Google マップのその日の臨時の時間が、THTH が前に出した値（か今出す値）と違うので、自動では出さずに止めました"
        "（Google が変えたか、人がマップの画面で直した見込み・Google の決まりで、Google 側の変更を自動で戻しません）。",
        "thth gbp show <店> で Google の今を確かめ、台帳の値で置き換えてよければ thth link send <台帳> --bundle <束> "
        "--by <名前> の二段確認で出してください（マップの値が正しければ台帳の行を直してください）。",
        _SEND,
        "マップの画面で直したときは台帳の行も同じに直すと、次の変化から自動のまま出せます。"),
    "register_link_row_changed": _entry(
        "束を作ったあとに台帳の行が変わったか、認めた記録が外れた・見直し日を過ぎたので、その束からは出しません。",
        "行を認め直して（thth register confirm）から thth link make <台帳> で出し直しの束を作ってください。",
        "thth link make <台帳>",
        "行を直したらすぐ認め直すと、新しい束が前の束の出ていない物を置き換え、同じ変化を二度出さずに済みます。"),
    "register_link_expired": _entry(
        "束の出さない期限（臨時は最後の日の終わり・催しは終わりの時刻）を過ぎたので、出しません。",
        "次の変化は台帳に行を足して thth link make <台帳> で束を作ってください（過ぎた物は出し直しません）。",
        "thth link make <台帳>",
        "VM の timer で thth link run を回しておくと、期限の前の同意のある物は出し遅れずに済みます。"),
    "register_link_confirm_mismatch": _entry(
        "--confirm の値が、いまの束の中身（台帳・画像・Google の今の臨時の時間）から作った digest と違います（何も出していません）。",
        "--confirm を付けずに打ち直して一段目の中身を見直し、新しく出た digest で打ち直してください。",
        _SEND,
        "一段目を見たあとに台帳やマップを直したら、もう一度一段目から通すと、見た物と出す物が同じになります。"),
    "register_link_account": _entry(
        "宣言の ig:・ig-story: の口座が THTH に無いか、Instagram の口座でないか、repo_dir が無いか、口座の project が"
        "台帳の project と違います（（）の語が project_mismatch なら、別の店の口座へは出しません）。何も出していません。",
        "thth account list で口座の名前と project を確かめ、.thth/linkage.json の to をこの店の Instagram の口座の名前に"
        "直してください。",
        _CHECK,
        "フィードとストーリーに同じ店の口座を書くと、1 つの変化が同じ店の IG にそろって出ます。"),
    "register_link_store_unbound": _entry(
        "宣言の gbp: の店が、この台帳の店の行（店.md）の gbp_location と結ばれていないので、出しません（別の店の"
        "マップを書き換えないため）。何も出していません。",
        "thth gbp locations で店の番号を確かめ、店.md の頭に gbp_location: locations/<数字> を書いて、"
        "thth register confirm <台帳> <店の行> --by <名前> の二段確認で認めてから打ち直してください。",
        "thth gbp locations",
        "店の行に結んでおくと、宣言の書き間違いで別の店のマップへ出ることがなくなります。"),
    "register_link_consent_unkept": _entry(
        "宣言のファイルに書かれた同意のうち、まだ THTH に控えていない物があります（ファイルの文字だけでは持ち主の"
        "同意と言えないので、その範囲も承認待ちのままです）。",
        "thth link consent <台帳> --by <名前> で同意の全文を見て、digest を付けて打ち直して控えてください。",
        "thth link consent <台帳> --by <名前>",
        "同意を書き直したら、そのたびに控え直すと、書き換えた範囲が知らないうちに自動になることがありません。"),
    "register_link_send_failed": _entry(
        "出し先が受け付けなかったので、その物は止めました（（）に Google か THTH の符丁があります・ほかの出し先は止めていません）。",
        "（）の符丁の直し方（thth gbp doctor・thth doctor <口座>）で直してから、thth link send <台帳> --bundle <束> --by <名前> "
        "で出し直してください。",
        _SEND,
        "thth gbp doctor と thth doctor を先に通しておくと、束の全部が一度にそろって出ます。"),
    "register_link_unknown": _entry(
        "送ったあとに答えが届かず、出たかどうかが分かりません。二度出さないために、その物は出し直していません。",
        "Google マップは thth gbp show <店>・thth gbp posts <店>、IG は thth inflight で今を確かめ、出ていなければ "
        "thth link send <台帳> --bundle <束> --by <名前> で出してください。",
        _SHOW,
        "確かめてから出し直すと、同じ知らせが二度並ばずに済みます。"),
}
