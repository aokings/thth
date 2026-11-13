"""断りの目録の項目（WordPress の口・5.16.0）。thth/refusal_catalog.py が読む。

WordPress（自分で置く型）へ記事を出す口（`thth/adapters/wordpress.py`・`thth/wp_html.py`・`hatena_entry.target`・
`thth principal add|auth`）が出す符丁。REST が閉じている・Authorization が落とされる・https でない・
セキュリティの部品が止めている、を分けて返す（設計の直し 8）。

`since` はまとめる者が版（5.16.0）を上げるまで今の版（`thth/VERSION` を越えない柵）。
"""
from __future__ import annotations

_C = ("cli",)
_CM = ("cli", "mcp")
_SINCE = "5.16.0"
_AUTH = "thth principal auth <主体> --force --by <名前>"


def _entry(why, fix, command, better, doors=_C, kind="refusal"):
    return {"kind": kind, "why": why, "fix": fix, "command": command, "better": better, "doors": doors,
            "since": _SINCE}


ENTRIES = {
    # ------------------------------------------------------------------ 主体の台帳と鍵
    "principal_invalid": _entry(
        "主体の台帳か命令の欄の組み合わせが、その主体の種類（はてな・WordPress）に合いません。",
        "主体の種類に合う欄で打ち直してください（WordPress は --kind wordpress --wp-user・はてなは --hatena-id）。",
        "thth principal show <主体>",
        "thth principal show で台帳の kind を見てから打つと、種類の取り違えが起きなくなります。"),
    "wp_user_invalid": _entry(
        "wp_user（WordPress のログイン名）が空か、使えない字が入っています。",
        "WordPress の「ユーザー」の画面に出るログイン名を --wp-user に入れて打ち直してください。",
        None,
        "記事を書くだけの「編集者」の利用者を作り、そのログイン名にすると、鍵が漏れたときの害が小さくなります。"),
    "wp_user_mismatch": _entry(
        "鍵で読んだ利用者が、主体の台帳の wp_user と違います（別の利用者のアプリケーションパスワードを入れた）。",
        "主体の wp_user の利用者でアプリケーションパスワードを作り直し、入れ替えてください。",
        _AUTH,
        "利用者ごとに主体を分けておくと、どの鍵で出したかが台帳から辿れます。"),
    # 5.18.2（5.16.0 監査 の 7）。since は版上げのときに直す（今の版を越えない柵）。
    "wordpress_home_host_mismatch": dict(_entry(
        "WordPress が記事の URL に使うホスト（設定の「サイトアドレス (URL)」）が、THTH が要求を送る入口のホストと"
        "違います。このまま出すと、記事は出ても THTH の台帳は「別の所に出た」と見て止まります（conflict）。",
        "主体のブログの行（principals/<主体>.json の blogs）の blog_id と、その口座の台帳（accounts/<口座>.json）の "
        "blog_id の両方を、記事の URL のホスト（上に出たホスト）にそろえてください。その行に site_url を書いているなら、"
        "それも https://<そのホスト> にするか欄を消します（片方だけ替えると主体と口座が噛み合わず、site_url だけ替えると "
        "site_url_invalid で止まります）。どちらも JSON を手で直します。REST の入口もサイトアドレスの上にあるので、"
        "そろえたホストで届きます。そろえたら thth principal auth <主体> --force --by <名前> か thth doctor <口座> で"
        "確かめます。替える前に承認した原稿は、送る先が変わったので approval_stale で止まり、出ません（記事は出ず、"
        "台帳も壊れません）。thth approve <ファイル> で、新しい送り先を見てから承認し直してください。",
        "thth principal show <主体>",
        "WordPress の「一般設定」の WordPress アドレスとサイトアドレスを同じホストにしておくと、入口と記事の URL が"
        "いつも合い、どちらのホストで読んでも同じサイトだと確かめやすくなります。"), since="5.18.0"),
    "site_url_invalid": _entry(
        "主体のブログの行の site_url（無ければ https://<blog_id>）が、公開の名前の https://<blog_id> の形になっていません。"
        "IP の形（192.168.1.10 など）・localhost は、鍵を内向きのアドレスへ送らないよう断ります（手元の試しの印があるときだけ loopback を許します）。",
        "blog_id と site_url を、サイトの公開のホスト名で https://<blog_id> の形に直してください（site_url の欄は消してもよく、無ければ https://<blog_id> を使います）。",
        "thth principal show <主体>",
        "サイトを https の公開の名前で開けるようにしておくと、鍵が平文や内向きの網へ流れる道がそもそも無くなります。"),
    # ------------------------------------------------------------------ 原稿（出し先ごとの形）
    "send_as_invalid": _entry(
        "send_as: の値が publish・draft のどれでもありません。",
        "send_as: は publish（公開・省略時）か draft（下書き）にしてください。",
        None,
        "下書きで出して WordPress の画面で見てから公開すると、崩れに先に気づけます。", _CM),
    "send_as_unsupported": _entry(
        "この出し先は、その公開の状態をまだ受けません（はてなブログは公開だけ・WordPress の予約は後の版）。",
        "send_as: を外すか、その出し先が受ける状態（WordPress は publish か draft）にしてください。",
        None,
        "予約したい時刻は publish_at に書けば、timer がその時刻に出します。", _CM),
    "send_as_create_only": _entry(
        "send_as: は作るとき（op: create）だけの欄です。直すときは、出したときの状態を変えません。",
        "op: update・delete の原稿からは send_as: を外してください。",
        None,
        "状態を替えたいときは WordPress の画面で替えると、承認した本文と別に扱えます。", _CM),
    "op_unsupported_for_media": _entry(
        "この出し先は、その操作をまだ受けません（WordPress の消す・ごみ箱へは後の版）。",
        "WordPress の記事を消すときは、WordPress の画面からごみ箱へ移してください。",
        None,
        "消す前に下書きへ戻すと、URL を残したまま読者から隠せます。", _CM),
    "wp_markup_unsupported": _entry(
        "記事に、WordPress へ送る変換が受けない記法があります（受けるのは段落・見出し・強調・リンク・箇条書き・コード）。",
        "（）に書いた記法を、受ける記法に書き換えてから commit してください（引用は段落に・表は箇条書きに など）。",
        None,
        "見出しと箇条書きで組むと、WordPress のテーマが変わっても崩れにくい記事になります。", _CM),
    "wp_link_scheme_unsupported": _entry(
        "リンクの行き先が http・https・mailto・相対のどれでもありません（javascript: などは送りません）。",
        "リンクの行き先を https の URL か、記事の中の相対のパスに直してください。",
        None,
        "https のリンクにそろえると、読者の端末の警告が出なくなります。", _CM),
    "wp_image_unsupported": _entry(
        "記事が手元の画像を参照しています。WordPress への画像の上げ（メディア）は後の版です。",
        "画像を WordPress のメディアに手で上げ、記事から画像の参照を外してから commit してください。",
        None,
        "記事が通ったあと WordPress の画面で画像を足すと、承認した本文と別に扱えます。", _CM),
    # ------------------------------------------------------------------ WordPress の答え
    "wordpress_rest_unavailable": _entry(
        "サイトの REST の入口（/wp-json/）が開いていません（パーマリンクが「基本」・部品や設定で REST を閉じている）。",
        "WordPress の「設定」→「パーマリンク」を「投稿名」などにし、REST を閉じる部品を止めてから打ち直してください。",
        "thth doctor <口座>",
        "ブラウザで <サイト>/wp-json/ を開いて JSON が出れば、THTH からも届きます。"),
    "wordpress_auth_header_dropped": _entry(
        "鍵を送りましたが、WordPress に届く前に Authorization の見出しが落とされています（サーバの設定）。",
        "Apache なら .htaccess に「SetEnvIf Authorization \"(.*)\" HTTP_AUTHORIZATION=$1」を足し、"
        "ホスティングの管理画面で Authorization を通す設定にしてから打ち直してください。",
        "thth doctor <口座>",
        "通ったあと thth doctor <口座> で users/me が読めれば、記事の公開も同じ道で通ります。"),
    "wordpress_auth_rejected": _entry(
        "WordPress が鍵を受け付けませんでした（アプリケーションパスワードが違う・取り消された・利用者名が違う）。",
        "WordPress の「プロフィール」でアプリケーションパスワードを作り直し、入れ替えてください。",
        _AUTH,
        "鍵の名前に THTH と付けておくと、取り消すときにどれか分かります。"),
    "wordpress_app_passwords_disabled": _entry(
        "このサイトか利用者で、アプリケーションパスワードが無効になっています（部品や設定で止めている）。",
        "サイトの管理者に、この利用者のアプリケーションパスワードを有効にしてもらってから入れてください。",
        _AUTH,
        "有効にするのは記事を書く利用者だけにすると、ほかの利用者の鍵は増えません。"),
    "wordpress_permission_missing": _entry(
        "鍵の利用者に、記事を作る・直す・公開する権限がありません（役割が投稿者より下）。",
        "その利用者の役割を「編集者」にするか、編集者の利用者で鍵を作り直して入れ替えてください。",
        _AUTH,
        "管理者ではなく編集者の鍵にすると、記事のほかの設定は鍵で変えられなくなります。"),
    "wordpress_blocked_by_security": _entry(
        "サイトの前のセキュリティの部品か WAF が、REST の書き込みを止めています（WordPress の答えではない断り）。",
        "セキュリティの部品の設定で、この利用者の REST（/wp-json/wp/v2/posts）を許してから打ち直してください。",
        "thth doctor <口座>",
        "許すのは REST の記事の入口だけにすると、ほかの守りは今のまま保てます。"),
    "wordpress_https_required": _entry(
        "サイトの入口が http です。鍵を平文では送りません。",
        "サイトを https にし、主体の site_url を https://<blog_id> にしてから打ち直してください。",
        "thth principal show <主体>",
        "https にすると、鍵も記事も途中で読まれなくなります。"),
    "wordpress_term_unknown": _entry(
        "原稿のカテゴリかタグ（# で始まる名前）が WordPress にまだありません（勝手には作りません）。",
        "WordPress の「投稿」→「カテゴリー」「タグ」で同じ名前を作るか、原稿の categories: を今ある名前に直してください。",
        None,
        "カテゴリは今ある 2〜3 個に絞ると、サイトの中で記事が辿られやすくなります。"),
    "wordpress_term_lookup_failed": _entry(
        "カテゴリとタグの名前を WordPress に確かめられなかったので、記事を送っていません。",
        "少し置いてからもう一度出してください（timer が次の回に出し直します）。",
        "thth doctor <口座>",
        "thth doctor <口座> が通るなら、次の回には名前も確かめられます。"),
    "wordpress_status_unexpected": _entry(
        "記事は WordPress に届きましたが、頼んだ状態（公開・下書き）と違う状態になりました（権限が足りず「レビュー待ち」など）。",
        "WordPress の画面で記事の状態を確かめ、thth inflight <口座> resolve で出たかどうかを決めてください。",
        "thth inflight <口座> resolve",
        "鍵の利用者を編集者にすると、頼んだ状態のまま出ます。"),
    # ------------------------------------------------------------------ はてなと共通の符丁の WordPress の分（監査 直したほうがよい 2）
    "wordpress_redirect_blocked": _entry(
        "WordPress のサイトが転送（3xx）を返しました。鍵は転送先に送っていません（記事を送ったあとなら、届いたかは分かりません）。",
        "主体のブログの行の site_url を、転送された先のホスト（www あり・なしなど）に合わせてください。"
        "記事を送ったあとに出たなら、WordPress の画面で確かめて thth inflight <口座> resolve で決めてください。",
        "thth principal show <主体>",
        "ブラウザで <サイト>/wp-json/ を開き、アドレス欄が変わらないホストを site_url にすると、転送で止まらなくなります。"),
    "wordpress_token_missing": _entry(
        "WordPress の主体のアプリケーションパスワード（鍵）がまだ入っていません。",
        "WordPress の「プロフィール」でアプリケーションパスワードを作り、端末から入れてください。",
        "thth principal auth <主体> --by <名前>",
        "記事を書くだけの「編集者」の利用者で鍵を作ると、鍵が漏れたときの害が記事だけに収まります。"),
    "wordpress_token_invalid": _entry(
        "WordPress の鍵のファイルが読めない形か、主体の wp_user と合っていません（アプリケーションパスワードの形でない値も入れません）。",
        "WordPress の「プロフィール」で作ったアプリケーションパスワード（英数 24 字）を入れ直してください。",
        _AUTH,
        "入れたあと thth doctor <口座> で鍵の利用者と役割が出れば、記事も同じ鍵で出せます。"),
    "wordpress_auth_unsupported": _entry(
        "WordPress の主体の auth が app_password ではありません（この版はアプリケーションパスワードだけを使います）。",
        "主体の台帳の auth を app_password にするか欄を消し、鍵を入れ直してください。",
        "thth principal show <主体>",
        "WordPress.com のサイトは後の版で受けます。自分で置いたサイトなら、アプリケーションパスワードがいちばん短い道です。"),
    "wordpress_response_invalid": _entry(
        "WordPress の答えが、THTH の読める形（REST の JSON・context=edit の raw）ではありませんでした。何も上書きしていません。",
        "少し置いてからもう一度出してください。記事を送ったあとに出たなら、WordPress の画面で確かめて thth inflight <口座> resolve で決めてください。",
        "thth doctor <口座>",
        "ブラウザで <サイト>/wp-json/wp/v2/posts を開いて JSON が出るなら、部品やテーマが答えを書き換えていないかを見ると早く片づきます。"),
    "wordpress_response_too_large": _entry(
        "WordPress の答えが大きすぎて、読み切りませんでした（記事を送ったあとなら、届いたかは分かりません）。",
        "WordPress の画面で記事を確かめ、送ったあとなら thth inflight <口座> resolve で決めてください。",
        "thth inflight <口座> resolve",
        "答えに大きな部品の出力が付くサイトなら、REST の答えに足す部品を外すと読み切れるようになります。"),
    "wordpress_endpoint_rejected": _entry(
        "WordPress のサイトの宛先が、つなぐ前の安全な宛先の検査を通りませんでした。断るのは、サイトの名前を引いた答えに"
        "内向きの IP（loopback・私用の網・link-local（169.254 のメタデータの口）・IPv4 互換の IPv6 など）が 1 つでも"
        "混ざるときと、URL の形（https・ユーザー名や # の無い形・名前に使えない字）が合わないときです。鍵は送っていません。",
        "サイトの名前の答え（DNS の A・AAAA）を dig などで引き、外の網の IP だけかを確かめてください。社内の DNS や"
        "ルーターが内向きの IP を返すサイト（同じ網の中のサイト）には、この道からは出せません。外の網から引ける名前の"
        " https の site_url にしてから打ち直してください。",
        "thth doctor <口座>",
        "DNS の答えを外の網の IP だけにそろえ、site_url を https の公開の名前にすると、この検査で止まる道がなくなります。"),
    "wordpress_rate_limited": _entry(
        "WordPress のサイト（か前のサーバ）が、短い間の回数の上限（429）を返しました。",
        "少し時間を置いてください（timer が次の回にもう一度出します）。",
        "thth doctor <口座>",
        "口座の min_interval_hours を空けると、サイトの上限に当たりにくくなります。"),
    "wordpress_http_status": _entry(
        "WordPress が、THTH の見分けない番号の HTTP の答え（1 行目の wordpress_http_<番号>）を返しました。",
        "番号を見て、500 番台ならサイトの誤りの記録を、400 番台なら部品やサーバの設定を確かめてから出し直してください。"
        "記事を送ったあとに出たなら thth inflight <口座> resolve で決めてください。",
        "thth doctor <口座>",
        "thth doctor <口座> が通るまで直してから出すと、記事の公開も同じ道で通ります。"),
    "entry_id_invalid": _entry(
        "配信の台帳の記事の ID が、出し先の記事の ID の形ではありません（台帳が手で書き換えられた見込み）。何も送っていません。",
        "thth deliveries <口座> で台帳の entry_id を見て、出し先の画面の記事の ID と合うか確かめてください。",
        "thth deliveries <口座>",
        "台帳は手で書き換えず、記事を取り込むときは thth conflict <口座> の道を使うと、ID が崩れなくなります。"),
    "edit_uri_mismatch": _entry(
        "配信の台帳の編集の URL が、主体のサイトと記事の ID から組んだ URL と違います（サイトか台帳が替わった）。何も送っていません。",
        "thth deliveries <口座> で台帳の edit_uri と、thth principal show <主体> のサイトのアドレスを見比べてください。",
        "thth deliveries <口座>",
        "サイトのアドレスを替えるときは、新しい主体の行で新しい配信として出すと、古い台帳と混ざりません。"),
    # ------------------------------------------------------------------ 告知（監査 直したほうがよい 1）
    "announces_target_draft": _entry(
        "告知が結ぶ記事は WordPress に下書きで出した配信です。下書きの URL は読者には開けないので、告知に入れません。",
        "WordPress の画面で記事を公開し、告知の原稿の announces: を外して、公開の URL を本文に直に書いてください。",
        None,
        "告知したい記事は send_as: を外して（公開で）出すと、告知が記事の公開を待ってから自動で URL を入れます。", _CM),
    "wordpress_transport_failed": _entry(
        "WordPress まで届いたか分からないまま、つながりが切れました（出し直しはしません）。",
        "WordPress の画面で記事が出たか確かめ、thth inflight <口座> resolve で決めてください。",
        "thth inflight <口座> resolve",
        "サイトの応答が遅いときは、口座の timeout を延ばすと切れにくくなります。"),
}
