"""断りの目録の項目（第 10 部: 台帳の機械・`thth/register/`・`thth register …`）。thth/refusal_catalog.py が読む。

符丁は `thth/register/errors.py` の `REASONS`。文は店（実店舗）にも自前のメディアにも読めるように書く。
原則 `docs/原則_前向きな断り.md`: なぜ・どうすれば通るか・どうすればもっと良くなるか。
since は版上げのときに直す（今の版を越えない柵があるので、今は今の版を置く）。
"""
from __future__ import annotations

_C = ("cli",)
_SINCE = "5.19.0"
_ADD = "thth register add <台帳> --path <フォルダ> --project <project> --kind media --by <名前>"
_CHECK = "thth register check <台帳>"
_SHOW = "thth register show <台帳>"
_CONFIRM = "thth register confirm <台帳> <行> --by <名前>"
_TRIGGERS = "thth register triggers <台帳>"
_DRAFT = "thth register draft <台帳> --to <口座> --row <行>"


def _entry(why, fix, command, better, kind="refusal", by_detail=None):
    entry = {"kind": kind, "why": why, "fix": fix, "command": command, "better": better, "doors": _C,
             "since": _SINCE}
    if by_detail:
        entry["by_detail"] = by_detail
    return entry


ENTRIES = {
    # ---------------------------------------------------------------- 段 1: 土台
    "register_args_invalid": _entry(
        "thth register に渡した値が決まりに合っていません（台帳の名前と project は英数字と _・.・-、行の番号は英小文字・数字・_・.・-、"
        "--by は誰が頼んだか）。",
        "1 行目の（）が指す引数を直して打ち直してください。",
        _ADD,
        "台帳の名前は <project>-media・<project>-store のように project と使い道をつなげると、名前だけでどの台帳か分かります。",
        by_detail={
            "register_name": {
                "fix": "台帳の名前を英数字と _・.・- だけで付け直して打ち直してください（空白・斜線・日本語は使えません）。",
                "command": _ADD,
                "better": "台帳の名前は <project>-media・<project>-store のように project と使い道をつなげると、名前だけでどの台帳か分かります。"},
            "project": {
                "fix": "--project に thth の project の名前（英数字と _・.・-）を入れて打ち直してください。",
                "command": _ADD,
                "better": "口座の台帳と同じ project の名前にそろえると、下書きをどの口座の queue に置くかで迷いません。"},
            "by": {
                "fix": "--by に、頼んだ人の名前を入れて打ち直してください（変更の記録に残ります）。",
                "command": _CONFIRM,
                "better": "代わりに回すときは、店主の名前でなく打った人の名前を --by に入れ、誰が認めたかは台帳の本文に書くと、あとで読んでも取り違えません。"},
            "row_or_trigger": {
                "fix": "thth register draft には --row <行> か --trigger <種類> のどちらか 1 つだけを付けて打ち直してください。",
                "command": _DRAFT,
                "better": "まず thth register triggers <台帳> で今出す頃のきっかけを見て、その行を --row で選ぶと、出しすぎずに済みます。"},
            "question": {
                "fix": "問いの番号（q-YYYYMMDD- と 6 字の形）を、thth register ask の出力からそのまま写して打ち直してください。",
                "command": "thth register questions <台帳>",
                "better": "ask の --json の question の欄を写すと、番号の写し違いが起きません。"},
            "answer_text": {
                "fix": "--asked と --said は 1 行・200 字までにし、鍵やパスワードらしい値を入れずに打ち直してください。",
                "command": "thth register answer <台帳> <問い> --result yes --by <名前>",
                "better": "答えの中身は台帳の行に書き、--said には要約の 1 文だけを入れると、問いの記録が読みやすく残ります。"},
            "row": {
                "fix": "行の番号（頭の id の欄）を英小文字・数字・_・.・- のまま写して打ち直してください。番号は thth register show <台帳> の左の列にあります。",
                "command": _SHOW,
                "better": "行の番号は店の名前と種類をつなげた形（shop-hours・special-2026-12-29 など）にすると、番号だけで中身が分かります。"},
        }),
    "register_name_taken": _entry(
        "その名前の台帳の控えが既にあります。上書きすると、前の台帳の認めた記録と取り違えるので、書きませんでした。",
        "別の名前で thth register add を打ち直すか、今の控えのまま thth register check <台帳> で中身を確かめてください。",
        _CHECK,
        "同じ台帳のフォルダを動かしただけなら、thth register remove で控えを消してから add し直すと、認めた行だけを認め直せば済みます。"),
    "register_missing": _entry(
        "その名前の台帳の控えがありません。",
        "先に thth register add で台帳のフォルダを控えてください（名前の綴りも確かめてください）。",
        _ADD,
        "控えたら、続けて thth register check で形を点検すると、直す所が 1 行ずつ分かります。"),
    "register_ledger_invalid": _entry(
        "台帳の控え（registers/<台帳>.json）か認めた記録（register_state/<台帳>/confirmed.json）が読めないか、決まった欄と違います。"
        "認めた記録が読めないときは、どの行も認めたと読まない側に倒しています。",
        "控えは register・project・path・kind の 4 つの欄だけにしてください。壊れたときは thth register remove <台帳> --by <名前> で"
        "控えを消し、thth register add から作り直して、要る行を認め直します。",
        "thth register remove <台帳> --by <名前>",
        "控えは手で直さず add で作り直すと、欄の取り違えが起きません。",
        by_detail={
            "drafts": {
                "fix": "THTH の側の下書きの控え（register_state/<台帳>/drafts.json）が読めないので、この下書きが台帳から作った物か"
                       "確かめられません。thth register draft で下書きを作り直すと控えも書き直されるので、新しい下書きを承認してください。",
                "command": _DRAFT,
                "better": "下書きの控えは THTH が書く物なので、手で開かずにおくと、承認のたびの読み直しが自動で済みます。"}}),
    "register_path_unusable": _entry(
        "--path の台帳のフォルダが無いか、フォルダでないか、symlink です（台帳の外を読まないため、symlink は辿りません）。",
        "台帳のフォルダの本当の道（絶対の道か ~ から）を --path に入れて、thth register add を打ち直してください。"
        "控えた台帳のフォルダを動かしたなら、thth register remove で控えを消してから add し直します。",
        _ADD,
        "台帳のフォルダは git の repo にしておくと、変えた跡が残り、下書きの頭に出所の commit が書けます。",
        by_detail={
            "inside_thth_state": {
                "fix": "台帳のフォルダが THTH の置き場（register_state/ か registers/ の中）にあります。そこは thth register remove と"
                       "thth leave が丸ごと消す所なので、台帳が消えてしまいます。台帳のフォルダを THTH の置き場の外"
                       "（持ち主の repo の中など）に移してから、--path にその道を入れて打ち直してください。",
                "command": _ADD,
                "better": "台帳のフォルダは git の repo にしておくと、変えた跡が残り、下書きの頭に出所の commit が書けます。"}}),
    "register_row_missing": _entry(
        "その番号の行が台帳にありません（1 行目の（）が番号）。",
        "thth register show <台帳> で行の番号を確かめて、打ち直してください。新しい行なら、台帳のフォルダに .md を 1 枚足します。",
        _SHOW,
        "行を足したら thth register check で形を点検すると、頭の欄の欠けがその場で分かります。"),
    "register_row_invalid": _entry(
        "台帳の行の形が決まりに合っていません（頭の 7 つの欄・印の語・日付の形・種類ごとの形のどれか）。",
        "表示された行ごとの直し方のとおりに .md を直して、thth register check <台帳> で点検し直してください。",
        _CHECK,
        "台帳の repo の CI で thth register check を回すと、形の崩れた行が commit の時点で分かります。",
        by_detail={
            "private_needed": {
                "fix": "下書きや貼る文を組むのに要る欄（臨時は日と休みか・時刻、催しは名前と始まりと終わり、営業の時間の変わりは"
                       "変わる日、品は名前）が、その行の private_fields に入っています。その欄を private_fields から外して"
                       "認め直してから、打ち直してください。",
                "command": _CONFIRM,
                "better": "外に出したくないのが理由や住所なら、その欄だけを private_fields に入れると、告知は出しつつ中身を守れます。"}}),
    "register_row_secret": _entry(
        "台帳の行に、鍵やパスワードらしい値があります。台帳は git の repo で、写しが残りやすいので、材料にもしません。",
        "その値を行から消して（鍵は thth の token set の命令で THTH の鍵の置き場へ）、thth register check <台帳> で点検し直してください。",
        _CHECK,
        "もう commit した値なら、行から消すのと一緒に、その鍵を発行し直すと安心です。"),
    "register_row_guess": _entry(
        "その行は「推測」の印のままです。推測の行の事実は、投稿・頁・貼る文の材料にしません（間違った事実を出さないため）。",
        "持ち主に中身が合っているかを聞き、合っていれば thth register confirm <台帳> <行> --by <名前> で「本人が認めた」にしてください。",
        _CONFIRM,
        "聞くときは推しを添えて、はい・いいえで答えられる形にすると、持ち主が 1 言で答えられます。"),
    "register_row_denied": _entry(
        "その行は、持ち主が「違う」と言った行です。材料にしません（行は残してあり、同じことを二度聞かないために使います）。",
        "正しい中身を持ち主に聞いて新しい行を書き（source: asked）、thth register confirm <台帳> <新しい行> --by <名前> で認めてもらってください。",
        _CONFIRM,
        "新しい行の本文に、違うと言われた行の番号を 1 行書いておくと、なぜ書き直したかが台帳に残ります。"),
    "register_row_unconfirmed": _entry(
        "その行は頭が「本人が認めた」でも、THTH の認めた記録が無いか、認めたあとに中身が変わっています。"
        "頭の印は手でも書き換えられるので、THTH は記録の指紋で見ています。",
        "今の中身を持ち主に見せて、合っていれば thth register confirm <台帳> <行> --by <名前> で認め直してください。",
        _CONFIRM,
        "中身を直すたびに認め直すのが手間なら、直す前に持ち主と一緒に中身を決めてから 1 回で認めると、往復が減ります。"),
    "register_row_review_passed": _entry(
        "その行は見直し日（review_by）を過ぎています。古いかもしれない事実は材料にしません。",
        "持ち主に今も合っているかを聞き、合っていれば review_by を先の日に直してから thth register confirm <台帳> <行> --by <名前> で認め直してください。",
        _CONFIRM,
        "営業の時間のように変わりにくい行は 3 か月先、催しのように日の決まった行はその日の翌日、と見直し日を中身に合わせると、聞く数が減ります。"),
    "register_row_not_public": _entry(
        "その行は外に出してよい印（public: true）がありません。",
        "持ち主が外に出してよいと言ったら、頭の public を true に直し、thth register confirm <台帳> <行> --by <名前> で認め直してください。"
        "出したくない欄だけがあるなら、行は public: true にして、その欄の名前を private_fields に並べます。",
        _CONFIRM,
        "住所や電話のような欄は、行ごと隠さず private_fields で欄ごとに隠すと、残りの事実は投稿に使えます。"),
    "register_row_retired": _entry(
        "その行は退役（status: 退役）しています。終わった事実は材料にしません。",
        "今の事実なら新しい行として書き、thth register confirm <台帳> <新しい行> --by <名前> で認めてもらってください。",
        _CONFIRM,
        "退役した行は消さずに残すと、前にいつ何があったかを次の年の投稿の材料に使えます。"),
    "register_confirm_mismatch": _entry(
        "--confirm の digest が、今の行の digest と違います。1 段目で見せたあとに行の中身が変わったか、別の行の digest です。",
        "--confirm を付けずにもう一度打って今の全文と digest を持ち主に見せ、合っていればその digest で確定してください。",
        _CONFIRM,
        "1 段目の全文を持ち主にそのまま見せてから確定すると、見せた物と認めた物が食い違いません。",
        by_detail={
            "confirm": {
                "fix": "--confirm を付けずにもう一度打って今の全文と digest を持ち主に見せ、合っていればその digest で確定してください。",
                "command": _CONFIRM,
                "better": "1 段目の全文を持ち主にそのまま見せてから確定すると、見せた物と認めた物が食い違いません。"},
        }),
    "register_row_unwritable": _entry(
        "台帳の行のファイルに書けませんでした（symlink・普通のファイルでない・書く権限が無い・置き場がいっぱい、のどれか）。何も変えていません。",
        "台帳のフォルダの持ち主と権限を確かめ、行を普通のファイルにしてから、同じ命令を打ち直してください。",
        _CHECK,
        "台帳のフォルダを THTH を動かす人の持ち物にしておくと、認める操作が頭の欄を書けます。"),
    # ---------------------------------------------------------------- 段 2: 実店舗の形・きっかけ・下書き
    "register_draft_account": _entry(
        "--to の口座の台帳が読めないか、その口座の queue の置き場（repo_dir と queue_dir）が使えません（1 行目の（）が欄）。",
        "thth list で口座の名前を確かめ、口座の台帳の repo_dir と queue_dir（repo の中の、. で始まらない道）を直してから打ち直してください。",
        "thth list",
        "台帳の project と同じ project の口座を --to に選ぶと、下書きが出す先の queue にそのまま入ります。"),
    "register_draft_nothing": _entry(
        "今は下書きにするきっかけがありません（行を選んだなら、その行のきっかけがまだ早いか、きっかけになる欄が無い）。",
        "thth register triggers <台帳> で今出す頃のきっかけを確かめ、出ている行を --row で選んで打ち直してください。",
        _TRIGGERS,
        "臨時の休みは 1 週間前、催しは 2 週間前からきっかけになります。行を早めに台帳に書いておくと、告知が間に合います。"),
    "register_draft_exists": _entry(
        "同じ行・同じきっかけ・同じ中身の下書きが、もう queue にあります。2 本目は作りませんでした。",
        "queue の下書きを整えて thth approve に通すか、要らなければその下書きを閉じてから、打ち直してください。",
        "thth approve <file>",
        "行の中身を直したなら、直した行を認め直してから作ると、別の下書きとして作れます。"),
    "register_draft_rejected": _entry(
        "作った下書きが queue の形の点検（thth lint）に通りませんでした（1 行目の（）が理由）。書いていません。",
        "行の名前や本文の長さを直して（媒体の字数に収まるように）、打ち直してください。",
        _DRAFT,
        "品や催しの名前は短くし、詳しい説明は思いの行に分けると、どの媒体の字数にも収まります。"),
    "register_draft_expired": _entry(
        "出さない期限（not_after）を過ぎているか、期限が消されたか、下書きを作ったときより後に動かされています。"
        "終わった催しや過ぎた休みを告げないため、下書きにも承認にもしません。",
        "まだ出す意味があるなら、台帳の行の日を今の事実に直して認め直し、thth register draft で作り直してください。過ぎた物なら、下書きを閉じてください。",
        _DRAFT,
        "催しの行は、終わったら status を退役にすると、次の年に同じ催しを書くときの見本として残ります。",
        by_detail={
            "not_after_moved": {
                "fix": "下書きの頭の not_after を、作ったときの値（thth register draft の出力の「出さない期限」）に戻してから、"
                       "thth approve を打ち直してください。日が延びたなら、台帳の行の日を直して認め直し、作り直します。",
                "command": _DRAFT,
                "better": "LLM に本文を整えてもらうときは「頭の行はそのまま残して」と添えると、期限も出所も変わらずに済みます。"}}),
    "register_row_changed": _entry(
        "下書きを作ったあとに、台帳の行の中身が変わっています（1 行目の（）が行の番号）。古い事実のまま出さないため、承認しません。",
        "thth register use <台帳> <行> で今の中身を確かめ、下書きを今の中身で作り直して（thth register draft）、承認し直してください。",
        "thth register use <台帳> <行>",
        "行を直したら、前の下書きは閉じてから作り直すと、queue に古い事実の下書きが残りません。",
        by_detail={
            "from_register_mismatch": {
                "fix": "下書きの頭の from_register の行が、THTH がこの下書きを作ったときの控えと違います。頭の from_register の 4 行を"
                       "作ったときのまま戻すか、出したい行で thth register draft を打って作り直してください。",
                "command": _DRAFT,
                "better": "LLM に本文を整えてもらうときは「頭の行はそのまま残して」と添えると、出所が変わらずに済みます。"}}),
    "register_recheck_unavailable": _entry(
        "この下書きの元の台帳の控えが、この機械にありません（1 行目の（）が台帳の名前）。店の事実は間違えたときの害が大きいので、読み直せないまま承認しません。",
        "台帳の控えがある機械（thth register add をした機械）で、同じ thth approve を打ってください。",
        _SHOW,
        "台帳を置く機械と承認する機械を同じにしておくと、承認のたびに読み直しが自動で済みます。",
        by_detail={
            "from_register_missing": {
                "fix": "名前は thth register draft の下書きの形なのに、頭に from_register の行が無く、この機械の控えにもありません。"
                       "どの行から作ったか分からないので読み直せません。台帳の控えのある機械で thth register draft を打って作り直してください。"
                       "台帳から作った物でなければ、ファイルの名前から -register- を外してから打ち直します。",
                "command": _DRAFT,
                "better": "LLM に本文を整えてもらうときは「頭の行はそのまま残して」と添えると、承認のときの読み直しが自動で済みます。"}}),
    # ---------------------------------------------------------------- 段 3: 聞いて埋める
    "register_question_log_invalid": _entry(
        "台帳のフォルダの問いの記録（問いの記録.jsonl）が読めないか、形が違います（symlink・大きすぎる・JSON の行でない）。"
        "聞いた数を数えられないので、新しく聞くことも答えを足すこともしません。",
        "問いの記録.jsonl を git で前の版に戻すか、別の名前に移してから、同じ命令を打ち直してください（新しい記録から数え直します）。",
        "thth register questions <台帳>",
        "問いの記録は手で直さず、台帳の repo に commit しておくと、壊れたときに前の版へ戻せます。"),
    "register_question_missing": _entry(
        "その番号の問いが、問いの記録にありません（1 行目の（）が番号）。",
        "thth register ask <台帳> --by <名前> の出力の問いの番号を写して、打ち直してください。",
        "thth register questions <台帳>",
        "聞いたらすぐ answer で記録すると、どの問いの答えかを取り違えません。"),
    "register_question_answered": _entry(
        "その問いには、もう答えが記録されています（1 行目の（）が番号）。答えは足すだけで書き換えません。",
        "答えが変わったなら、台帳の行を直して認め直してください。もう一度聞くなら、次の ask で新しい問いの番号が出ます。",
        "thth register ask <台帳> --by <名前>",
        "答えを書き換えずに新しい問いとして残すと、いつ答えが変わったかが記録に残ります。"),
    "register_log_unavailable": _entry(
        "変更の記録（admin_log）に書けない置き場なので、何も変えませんでした。",
        "THTH の置き場（$THTH_ROOT）の権限と空きを確かめて、同じ命令を打ち直してください。",
        "thth doctor",
        "thth doctor で置き場を一度点検しておくと、ほかの命令も同じ理由で止まらずに済みます。"),
    # ---------------------------------------------------------------- 段 4: 外の場所に手で貼る束
    "register_paste_kind": _entry(
        "外の場所に貼る文を作れるのは、臨時（special）・営業の時間（hours）・催し（event）の行だけです（1 行目の（）が渡した行）。",
        "貼りたい中身を臨時・営業の時間・催しのどれかの行にして、thth register paste <台帳> <その行> を打ち直してください。",
        "thth register paste <台帳> <行>",
        "品や店の紹介は、自前のメディアの記事（thth register draft）から外の場所へリンクで届けると、文を二か所で直さずに済みます。"),
    "register_paste_nothing": _entry(
        "この台帳に外の場所の行（kind: place）が 1 枚も無いので、どこに貼るかが分かりません。",
        "店の情報が載っている場所（Google マップ・食べログなど）ごとに、kind: place・place: <場所の番号> の行を台帳に足して、"
        "thth register check で形を確かめてから打ち直してください。",
        _CHECK,
        "場所の行に admin_by（管理画面を誰が持つか）を書いておくと、貼る人を毎回聞かずに済みます。"),
    "register_place_not_place": _entry(
        "1 行目の（）の行は外の場所の行（kind: place）ではありません。",
        "thth register show <台帳> で kind が place の行の番号を見て、その番号で打ち直してください。",
        _SHOW,
        "場所の行の番号は place-tabelog・place-google のように場所の名前を入れると、取り違えにくくなります。"),
    "register_compare_empty": _entry(
        "比べる文が空か、長すぎます（5000 字まで）。",
        "外の場所の頁の、営業の時間やお知らせの欄の文だけを写して、標準入力に渡して打ち直してください。",
        "thth register compare <台帳> <場所の行> --for <行> < 写した文",
        "欄の文だけを写すと、ほかの欄の日付と取り違えずに比べられます。"),
    # ---------------------------------------------------------------- 台帳の器 段 1: 読んで、同じ対象を指す
    "register_kind_invalid": _entry(
        "台帳の .thth/ の宣言（種類の宣言・憲法・番号の表）が決まった形に合っていません（1 行目の（）が、どの宣言の"
        "どの語か）。THTH は宣言を語と道の型として読むだけで、宣言に書いた命令は実行しません。知らない欄も断ります。",
        "（）の語の欄を直してください。種類の宣言に書けるのは schema・kind・place・format・records・max_bytes・"
        "rows_at・id・questions・pointers・units・note だけで、値は欄の名前・道の型・語です。直したら打ち直してください。",
        "thth register kinds <台帳>",
        "宣言は台帳の repo に commit しておくと、形を変えたこと（意味を変えたこと）が跡に残り、持ち主が確かめられます。"),
    "register_yaml_refused": _entry(
        "YAML の記録に、器が読む範囲の外の書き方があります（同じ段の同じ鍵・&と*の別名・2 つめの文書・!! の型の札・<<: "
        "のどれか。語と行は --json の problems）。黙って読み違えないように、中は読まずに断りました。版は取ってあります。",
        "その書き方を開いて書き直してください（別名は値を書き写す・型の札は外して字で書く・2 つめの文書は別の枚に分ける・"
        "重なる鍵は 1 つにまとめる）。直したら thth register check <台帳> で点検し直してください。",
        _CHECK,
        "日付や true・false も字のまま読み、型は種類の宣言で見るので、引用符を足す直しは要りません。"),
    "register_record_invalid": _entry(
        "宣言した種類の記録の読みに問題があります（番号の形・同じ番号が 2 つ・表の列の数・大きさの上限・UTF-8 のどれか。"
        "記録ごとの語は --json の problems）。同じ番号の記録は、黙って 1 つにしません。",
        "記録ごとの語のとおりに元のファイルを直すか、種類の宣言（番号の取り方・max_bytes）を直して、"
        "thth register check <台帳> で点検し直してください。",
        _CHECK,
        "番号は一度付けたら変えないでください。番号で判断が付くので、付け直すと前の確かめが別の記録へ移りません。"),
    "register_record_missing": _entry(
        "その記録（または単位・値）が台帳にありません（1 行目の（）が、何を探したか）。記録は <種類>:<番号> で指します。",
        "thth register check <台帳> --json の catalog で番号を確かめて、打ち直してください。",
        _CHECK,
        "同じ番号でも種類が違えば別の記録です（枚と点の表など）。種類の名前から付けて指すと取り違えません。"),
    "register_unit_ambiguous": _entry(
        "渡した鍵（か番号）に当たる単位が 2 つ以上あります。器は鍵で決まらない単位を黙って 1 つにしません"
        "（並べ替えや挿入で、確かめが別の値に移らないように）。",
        "--json の candidates の指し先（kc: で始まる、鍵と中身の指紋）を 1 つ選んで、--unit <指し先> で打ち直してください。",
        "thth register links <台帳> <記録> --units",
        "元の表に、その 2 つを分ける欄（コース・内訳など）があれば鍵に足すと、鍵だけで指せるようになります。"),
    "register_link_invalid": _entry(
        "指す欄に、指す先の無い番号か、輪があります（輪を禁じるのは 含む・親・置き換える だけ。主張どうしの往復は止めません）。"
        "どれかは --json の dangling と cycles。",
        "指す先の番号を直すか、指す先の記録を足してください。輪は、どこかの 1 本を外してください。"
        "直したら thth register check <台帳> で点検し直してください。",
        _CHECK,
        "台帳の repo の CI で check を回すと、指す先の切れが commit の時点で分かります。"),
    "register_judgment_invalid": _entry(
        "台帳の判断の記録（.thth/judgments.jsonl）の 1 行が、決まった形に合いません（1 行目の（）が行）。",
        "その行を、git の前の版から戻してください（判断の記録は足すだけで、手で書き換えない物です）。",
        _CHECK,
        "判断の記録を台帳の repo に commit しておくと、壊れたときに前の版へ戻せます。"),
    "register_judgment_same_person": _entry(
        "中身を変えた者と、結び直す者が同じです。同じ者の判断は、別の者の確かめに数えません。",
        "中身を変えていない別の者に、新しい版を見てもらって結び直してください。",
        _CHECK,
        "直しと確かめを同じ便に入れると、頁から外れる間を作らずに済みます。"),
    "register_carry_unnamed": _entry(
        "中身を変えた者が分からないので、結び直す者が別の者かを確かめられません。別の者の確かめに数えられないため、"
        "結び直しませんでした。",
        "中身を変えた者（commit の書き手や、取り込んだ道具の名前）を名指しして、もう一度結び直してください。",
        _CHECK,
        "取り込みの道具が、直した者の名前を変更の記録に残すようにしておくと、結び直しのたびに調べずに済みます。"),
    # ---------------------------------------------------------------- 台帳の器 段 2
    "register_adopt_unusable": _entry(
        "取り込みか戻す出口か束の、フォルダの組み合わせが使えません（1 行目の（）の語: source は元が無い・"
        "inside は片方がもう片方の中・not_empty は書き先が空でない・other_source は台帳が別の元から取り込まれた・"
        "symlink は範囲の中か範囲の道の途中か、書き先とその .thth/ に symlink がある・thth_in_source は範囲が .thth/ を"
        "含む・plan は範囲の JSON の形・spelling は範囲の道の綴りが実在の綴りと大文字小文字か Unicode の正規化で違う・"
        "too_large は種類の宣言の上限を超える枚か、どの種類の宣言にも当たらない 64 KB を超える枚がある・unreadable は"
        "権限で読めないフォルダかファイルがある（読めない物を「消えた」と読まない）。元にも台帳にも何も書いていません。",
        "語に合わせて直してください: 書き先は新しい空のフォルダにする・元の repo の外に置く・symlink と .thth/ は"
        "範囲の exclude に足す（書き先の symlink は本物のフォルダにする）・spelling は --json の actual の綴りで範囲を"
        "書く・too_large は --json の files の種類の宣言に max_bytes を書く（kind が null の枚は、その道の種類を宣言して"
        " max_bytes を書くか、範囲の exclude に足す）・unreadable は --json の path を読めるようにするか、範囲の exclude に"
        "足す・範囲は {\"schema\": \"thth.adopt_plan/1\", "
        "\"include\": [...], \"exclude\": [...]} の形にする。",
        "thth register adopt <元> --to <新しいフォルダ> --plan <範囲.json> --by <名前>",
        "影の台帳は ~/Documents/THTH/shadow/<メディア>/ のように repo の外の決まった置き場に作ると、元と取り違えません。"),
    "register_batch_pending": _entry(
        "台帳に、途中で止まったまとめての入れ替えが残っています（1 行目の（）が何の入れ替えか）。混ざった姿を読まない"
        "ように、器の読み手は入れ替えの前の姿だけを見せ、ほかの書き込みは止めています。",
        "続けるなら thth register resume <台帳> --continue --by <名前>（同じ命令を打ち直してもよい）、戻すなら"
        " thth register resume <台帳> --rollback --by <名前> を打ってください。別の者が打ってもかまいません。",
        "thth register resume <台帳> --continue --by <名前>",
        "--json の batch・what・by・at で、誰がいつ何を入れ替えていたかが分かります。打ち直す前に、その者に一言かけると"
        "二重に進めずに済みます。"),
    "register_batch_conflict": _entry(
        "入れ替えの途中か前に、台帳のファイルが器の知らない中身に変わっていました（1 行目の（）: moved は前でも後でも"
        "ない中身・base は束を作った時から台帳が動いた・symlink は書く先か読む先の途中の段（.thth/ の中も）が symlink で、"
        "台帳の外を指しうる・busy は読んでいる間に入れ替えが何度も始まった）。黙って上書きせず、台帳の外にも書かずに"
        "止めました。",
        "moved と base は、--json の path のファイルを誰が書いたかを確かめ、その書き込みを済ませてから、thth register keep "
        "で今を控え、束を今の台帳から作り直して打ち直してください。symlink は、--json の path の symlink を本物の"
        "フォルダかファイルに置き換えてから打ち直してください。busy は、入れ替えが済んでから打ち直してください。",
        "thth register diff <台帳> last..now",
        "元の形式の道具と器の入れ替えを同じ時間に走らせないと、この断りは出ません。"),
    "register_kept_missing": _entry(
        "器の控え（.thth/kept/）に、求めた前の版がありません（1 行目の（）の語）。取り出せる前の版は、器が取り込んだか"
        "控えた時点からだけで、それより前と、控えと控えの間の版は取り出せません。",
        "thth register keep <台帳> --list で控えた時点を並べ、その目録の指紋の頭（8 字以上）か first・last で打ち直して"
        "ください。控えが壊れている（object_broken・snapshot_broken）ときは、元からもう一度取り込んでください。",
        "thth register keep <台帳> --list",
        "元の形式の道具で直したあとに thth register keep を打つ習いにすると、git の外の台帳でも前の版を取り出せます。"),
    "register_record_vanished": _entry(
        "前の時点にあった記録の番号（かファイルか、内側の行の明示の番号。--json の units_vanished）が、後の時点にありません。番号が消えると、その番号への判断と、"
        "指していた記録がたどれなくなります。",
        "消すつもりでなければ、thth register restore <台帳> --at <前の控え> --to <フォルダ> で前の版を書き出して戻して"
        "ください。退役させたいなら、記録を消さずに状態を退役にしてください。",
        "thth register diff <台帳> <前>..<後> --json",
        "名前を変えただけなら、番号の表（.thth/ids.json）に新しい場所を書くと、番号を引き継げます。"),
    "register_write_unsafe": _entry(
        "表の 1 行を書くと、触っていない行のバイトが変わるか、書いた行が読み直せない形になります（1 行目の（）の語: "
        "other_rows・no_final_newline・unknown_field・id_field・id_exists・id_form・row_width・not_table・which_table・"
        "table_shape・not_utf8）。表には何も書いていません。",
        "語に合わせて直してください: 最後の行に改行が無い表は、元の道具で改行を足してから・欄の名前は表の見出しのとおりに・"
        "番号の欄は直さずに新しい行として足す・番号が重なるなら別の番号で。",
        "thth register links <台帳> <種類:番号>",
        "1 行ずつ直すより、束をまとめて入れる（thth register merge）ほうが、止まっても前か後のどちらかに決まります。"),
    # ---------------------------------------------------------------- 台帳の器 段 3
    "register_outlet_unknown": _entry(
        "その出し口が、台帳の .thth/outlets.json に宣言されていないか、宣言の形が崩れています（1 行目の（）の語: "
        "outlet は名前が無い・outlets: に続く語は宣言の崩れた所）。材料は書いていません。",
        "thth register kinds <台帳> で種類の名前を確かめ、.thth/outlets.json の outlets に出し口を足すか直してから、"
        "打ち直してください（出し口ごとに audience・kinds・requires・fact・reservation を書きます）。",
        "thth register kinds <台帳>",
        "読者に出る出し口と内の道具が読む出し口を分けて宣言すると、出さない層が公開の材料へ混ざりません。"),
    "register_use_not_usable": _entry(
        "その記録は、この出し口に、頼んだ「何として」では使えません（1 行目の（）の語: stopped は止めている・"
        "not_accepted は判断はあるが受け入れていない・no_judgment は判断が無い・no_inspection_for_new_version は"
        "中身が変わって新しい版への検収が無い・against_changed は照らした根拠が変わった・expired は期限が切れた・"
        "not_permitted は出してよいの判断が無い・reason_kind_not_allowed は留保にしてよい理由の種類でない・"
        "only_reservation は留保としてだけ使える・no_seal はまだ封が無い）。手元でも CI でも同じ答えです。",
        "語に合わせて進めてください: 判断が無い・古いなら thth register judge で新しい版へ判断を足し（直した者と別の者なら"
        " --carry で結び直せます）、thth register accept で受け入れて封をし直す。留保としてだけ使えるなら --as reservation で。",
        "thth register judge <台帳> <種類:番号> --question <問い> --conclusion good --by <名前>",
        "直しと検収を同じ便に入れると、頁から外れる間を作らずに済みます。"),
    "register_material_stale": _entry(
        "書き出し先に、この出し口の一覧に無い旧い材料のファイルがあります（--json の stale）。読まれ続けると、"
        "外したはずの記録が頁に残るので、書くのをやめました。THTH はファイルを消しません。",
        "一覧に無いファイルを、持ち主の手で書き出し先から外して（git の repo なら git rm）、打ち直してください。",
        "thth register export <台帳> --outlet <出し口> --to <先> --as-of <日時> --check",
        "書き出し先を出し口ごとのフォルダに分けると、旧い材料がほかのファイルと混ざりません。"),
    "register_seal_missing": _entry(
        "この出し口は、まだ受け入れて封をしていません。封の中の判断だけで材料を作るので、封が無いと材料を作れません。",
        "thth register accept で、今の判断を持ち主に見せて受け入れ、封をしてください（二段）。",
        "thth register accept <台帳> --outlet <出し口> --to <先> --as-of <日時> --by <名前>",
        "初めての出し口は、thth register judge --baseline で今の版を基準として受けてから accept すると、頁を変えずに始められます。"),
    "register_seal_unverified": _entry(
        "公開の直前の確かめが赤です（1 行目の（）の語が最初の赤。全部は --json の problems）。受け入れていない判断・"
        "封のあとの止める・期限・中身の変わり・世代の戻り・判断の記録の消え・別の鍵の封・宣言の変わりのどれかがあります。",
        "語に合わせて直してください: 新しい判断や止めるを足したなら thth register accept で封をし直す・世代を戻したなら"
        "最後の世代の封と材料に戻す・判断の記録の行を消したなら git の前の版から戻す・公開鍵は CI の変数"
        " THTH_REGISTER_SEAL_PUBLIC_KEY に持ち主の公開鍵を置く（repo の中の鍵のファイルは読みません）。",
        "thth register verify <台帳> --outlet <出し口> --material <先> --previous <前の封>",
        "配る前に手元で同じ verify を回すと、CI で止まる前に気づけます。夜に期限が切れて朝の配信が止まるのを避けたい出し口は、"
        "出し口の宣言で on_narrow を drop にすると、CI が外して配ります。"),
    "register_seal_key_missing": _entry(
        "封の鍵か、封に使う openssl（3 か 4）がありません（1 行目の（）の語: key は持ち主の秘密の鍵が無い・"
        "public_key は確かめに使う公開鍵が無い・openssl は決まった場所に OpenSSL 3・4 が無い・public_key_form は公開鍵の形が違う）。",
        "持ち主の機械で thth register key init を 1 回打ち、出た公開鍵を CI の変数 THTH_REGISTER_SEAL_PUBLIC_KEY に"
        "置いてください（置くのは持ち主の手か承認で）。openssl が無い機械では、Homebrew などで OpenSSL 3 以上を入れてください。",
        "thth register key init",
        "公開鍵の指紋（thth register key show）を控えておくと、CI に置いた鍵が持ち主の物かを見分けられます。"),
    "register_seal_key_unsafe": _entry(
        "持ち主の秘密の鍵の置き場が使えません（1 行目の（）の語: inside_register は台帳か台帳を含む repo の中にある・"
        "key_mode は持ち主のほかにも読める権限か symlink・key_exists はもう鍵がある）。鍵の値は出していません。",
        "秘密の鍵は ~/.config/thth/ に置き、権限は 0600 にしてください。台帳や repo の中に置かないでください。"
        "もう鍵があるなら、作り直さずにそのまま使ってください（替えると CI の公開鍵も替えることになります）。",
        "thth register key show",
        "XDG_CONFIG_HOME を台帳の repo の中に向けないでおくと、鍵が commit に紛れ込みません。"),
    "register_common_unusable": _entry(
        "共通の台帳が使えません（1 行目の（）の語: path はフォルダが無い・common は --common が無い・rows は表の行の"
        "記録で、版ごとに取り出せない）。採っていません。",
        "--common に共通の台帳のフォルダを渡してください。共通に置く記録は、1 つのファイル = 1 つの記録の種類にしてください。",
        "thth register take <台帳> --common <共通の台帳> --record <種類:番号> --by <名前>",
        "共通の台帳を git に置き、直すたびに thth register keep で控えると、前の版を採っているメディアも材料を作り直せます。"),
}
