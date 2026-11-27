// 生成物: tools/render_refusals.py が断りの目録（thth/refusal_catalog*.py）の worker の口から書き出す。手で直さない。
// 符丁 → {why: なぜ, fix: 通すには, better: もっと良く（無ければ null）}。relay.js の reply が断りの答えに足す。
// 穴（<口座> などの型）は埋めない。文を直すときは目録の部を直して python3 tools/render_refusals.py を打つ。
export const REFUSALS=Object.freeze({
 "access_denied": {
  "why": "持ち主が同意の頁で「許さない」を選びました。鍵は作っていません。",
  "fix": "つなぐなら、AI の側からコネクタの追加をもう一度始め、同意の頁で許してください。",
  "better": "同意の頁で「読むだけ」を選んで許すと、書く道具を使わずに試せます。"
 },
 "account_revoked": {
  "why": "この添付の口座は、thth.me で取り消されています。",
  "fix": "使える口座で thth_media_upload_url から新しく始めてください（口座を戻すときは、招待か `thth login` で登録し直してから）。",
  "better": "取り消した口座の添付は使い回さず今の口座で上げ直すと、出す口座の取り違えが起きません。"
 },
 "activity_unavailable": {
  "why": "thth.me のアクティビティの画面が、いま答えを返せません。",
  "fix": "少し置いてから https://thth.me/activity を開き直してください。続くなら、命令と時刻を添えて `thth report file` で開発のセッションへ知らせてください。",
  "better": "画面を開き直す前に網のつながりを確かめると、どちらの側の止まりかを切り分けられます。"
 },
 "api_unavailable": {
  "why": "thth.me の遠くの道（/api/v1）が、いま依頼を受け付けられません。",
  "fix": "少し置いてから、同じ命令をもう一度打ってください。続くなら、命令と時刻を添えて `thth report file` で開発のセッションへ知らせてください。",
  "better": "書く命令の前に読むだけの命令で通るかを確かめると、止まっている間に書きかけを残しません。"
 },
 "binding_changed": {
  "why": "データ削除の依頼の中身が、確かめた時と変わっています。",
  "fix": "VM で `thth admin deletion sync` を打ち直して、依頼を読み直してから確かめ直してください。続くなら、命令と時刻を添えて `thth report file` で開発のセッションへ知らせてください。",
  "better": "読む・確かめる・済ませるを 1 つの依頼ずつ続けて進めると、中身の食い違いが起きません。"
 },
 "cleanup_binding_mismatch": {
  "why": "同じ添付を消す予約が、違う口座か違う時刻で既に置かれています（VM と thth.me の食い違い）。",
  "fix": "VM の記録を確かめてから、元の口座と時刻で予約を置き直してください。続くなら、命令と時刻を添えて `thth report file` で開発のセッションへ知らせてください。",
  "better": "予約は VM の 1 か所からだけ置くと、食い違いが起きません。"
 },
 "cleanup_not_due": {
  "why": "この添付は、まだ消す時刻になっていないか、別の口座のものです。",
  "fix": "消す時刻を過ぎてから、VM で `thth admin media cleanup-retry` をもう一度打ってください。",
  "better": "消すのは時刻が来れば自動で進むので、やり直しは失敗が出たときだけにすると手間が省けます。"
 },
 "cleanup_registration_unconfirmed": {
  "why": "この添付をあとで消す予約が、まだ確かめられていないので、受け取りを始めません。",
  "fix": "少し置いて、thth_media_upload_url から新しく始めてください。続くなら、命令と時刻を添えて `thth report file` で開発のセッションへ知らせてください。",
  "better": "添付は投稿の予定より早めに上げておくと、予約の確かめを待っても間に合います。"
 },
 "cleanup_retry_unavailable": {
  "why": "添付の削除のやり直しに使う控えが、thth.me でいま読めませんでした。",
  "fix": "少し置いて、VM で `thth admin media cleanup-retry` をもう一度打ってください",
  "better": null
 },
 "code_in_use": {
  "why": "この login の受け口は、まだ期限の内の別の login を待っています。",
  "fix": "前に出した login の画面で許可を終えるか、10 分の期限が切れてから `thth login` をやり直してください。",
  "better": "`thth login` は 1 つの端末で 1 回ずつ打つと、待っている login が重なりません。"
 },
 "confirm_mismatch": {
  "why": "リモート MCP の書く道具の 2 段目に付けた confirm が、1 段目で返した値と違います。何もしていません。",
  "fix": "同じ引数で confirm を付けずに呼び、1 段目で返る新しい値を付けて呼び直してください",
  "better": "1 段目と 2 段目の間は台帳や原稿を触らずに続けると、一度で通ります"
 },
 "confirm_required": {
  "why": "リモート MCP の書く道具は 2 段で動きます。この接続で 1 段目を受け取っていないか、10 分を過ぎたか、引数が 1 段目と違います。何もしていません。",
  "fix": "同じ引数で confirm を付けずに呼んで 1 段目を受け取り、中身を持ち主に見せてから、返った値を confirm に付けて呼んでください",
  "better": "持ち主が消すと言った投稿だけを頼むと、消してはいけない投稿を消す取り違えが起きません"
 },
 "connectors_unavailable": {
  "why": "コネクタの一覧を、いま読めませんでした（何も取り消していません）。",
  "fix": "少し置いてから、https://thth.me/connectors をもう一度開いてください。続くなら、命令と時刻を添えて `thth report file` で開発のセッションへ知らせてください。",
  "better": "すぐ止めたいときは AI の側でコネクタを外すと、その token も取り消しになります。"
 },
 "deletion_capacity": {
  "why": "データ削除の依頼の控えが上限（1000 件）に達していて、新しい依頼を置けません。",
  "fix": "VM で `thth admin deletion sync` を打って控えを読み進めてから、もう一度送ってください。",
  "better": "VM の `thth admin deletion sync` を定期の timer で回しておくと、控えが上限まで溜まりません。"
 },
 "deletion_unavailable": {
  "why": "thth.me のデータ削除の受け口が、いま答えを返せません。",
  "fix": "少し置いてから、もう一度送ってください。続くなら、命令と時刻を添えて `thth report file` で開発のセッションへ知らせてください。",
  "better": "依頼のときに返った確かめの URL（data-deletion-status）を控えておくと、届いたかどうかを後から見られます。"
 },
 "invalid_client_metadata": {
  "why": "登録しようとした client の情報（redirect_uris など）が、受け付ける形と違います。",
  "fix": "redirect_uris を https の URL（手元の試しなら http://localhost）で 1〜8 個にして、登録し直してください。",
  "better": "client の身元を https の URL の文書（CIMD）で示すと、登録の手間なしにつながります。"
 },
 "invalid_grant": {
  "why": "認可の code か refresh token が、使い済み・期限切れ・取り消し済み、または PKCE の verifier と合いません。",
  "fix": "AI の側でコネクタをつなぎ直してください（同意の頁からやり直すと新しい code が出ます）。",
  "better": "refresh token は 1 回ごとに新しい物に替わるので、受け取った新しい物だけを使うと続けてつながります。"
 },
 "invalid_key": {
  "why": "Authorization: Bearer の鍵が無いか、thth.me に登録されていません（取り消されたか、違う鍵です）。",
  "fix": "`thth login` でブラウザから許可し直して新しい鍵を取り、その鍵で送り直してください",
  "better": "鍵は `thth login`（ブラウザで許可）で取ると、手で貼る間違いが起きません"
 },
 "invalid_media_request": {
  "why": "添付の受け口への要求の形が違います（query を付けた・Content-Type が JSON でない など）。",
  "fix": "thth_media_upload_url が返した URL を query を付けずにそのまま使い、送り直してください。",
  "better": "受け口の URL は手で組み立てず返った値を写すと、形の違いが起きません。"
 },
 "invalid_range": {
  "why": "Range の範囲が添付の大きさの外か、読めない形です。",
  "fix": "Range を外すか、応答の content-range に書いた大きさの内側で bytes=始め-終わり を頼み直してください",
  "better": "Range を付けずに一度で取ると、往復が減ります"
 },
 "invalid_request": {
  "why": "要求の形が受け付ける形と違います（path・query・Content-Type・本文の鍵のどれか）。",
  "fix": "URL を出した命令の出力どおりの path と Content-Type で、決まった鍵だけを送り直してください",
  "better": "HTTP を直接組まず thth の CLI か MCP の道具から呼ぶと、形は道具が整えます"
 },
 "invalid_token": {
  "why": "Authorization: Bearer の access token が、期限切れか取り消し済みか、形が違います。",
  "fix": "refresh token で新しい access token を取るか、AI の側でコネクタをつなぎ直してください。",
  "better": "access token は 1 時間で切れるので、切れる前に refresh で替えると呼び出しが止まりません。"
 },
 "invite_exists": {
  "why": "その招待は thth.me に既に作られています（VM の relay の 2 度目の作成）。",
  "fix": "VM で `thth admin invite list` を見て、今ある招待を使うか取り消してから作り直してください。",
  "better": "招待は 1 人 1 本にすると、誰が使ったかを追えます。"
 },
 "invite_unavailable": {
  "why": "その招待は期限切れか取り消し済みか、招待の受け口がいま答えを返せません。",
  "fix": "招待した人に、新しい招待を頼んでください（https://thth.me/privacy/ の Contact からも頼めます）。",
  "better": "招待は使う日に合わせて作ってもらうと、期限切れを避けられます。"
 },
 "invite_used": {
  "why": "その招待は既に使われて、口座の用意が済んでいます。",
  "fix": "新しい人には、VM で `thth admin invite create` で別の招待を作ってください。",
  "better": "招待は 1 人 1 本にすると、誰が使ったかを追えます。"
 },
 "key_expired": {
  "why": "thth.me の鍵の期限が切れています。",
  "fix": "`thth login` でブラウザから許可し直して新しい鍵を取り、その鍵で送り直してください",
  "better": "期限の近い鍵を https://thth.me/activity で早めに発行し直すと、作業の途中で止まりません"
 },
 "login_unavailable": {
  "why": "thth.me が、いまブラウザ式の login を受け付けられません。",
  "fix": "少し置いて `thth login` をやり直すか、鍵が手元にあれば `thth login --stdin` で入れてください",
  "better": null
 },
 "mcp_unavailable": {
  "why": "thth.me のリモート MCP が、いま頼みを受け付けられません。",
  "fix": "少し置いてから、同じ道具をもう一度呼んでください。続くなら、命令と時刻を添えて `thth report file` で開発のセッションへ知らせてください。",
  "better": "書く道具の前に読む道具（thth_board など）で通るかを確かめると、止まっている間に書きかけを残しません。"
 },
 "media_already_acknowledged": {
  "why": "この添付の公開は、既に媒体に出したと受け取り済みです。",
  "fix": "やり直しは要りません。次の投稿には thth_media_upload_url から新しい添付を上げてください。",
  "better": "同じ添付を別の投稿に使うときも新しく上げると、公開の URL の期限が投稿ごとにそろいます。"
 },
 "media_completion_unconfirmed": {
  "why": "添付の完了（分けて送った部分をつなぐ所）が済んだかどうか、確かめられませんでした。",
  "fix": "少し置いて thth_media_complete をもう一度呼んでください。続くなら thth_media_upload_url から新しく始めてください。",
  "better": "完了は送り込みがすべて終わってから 1 回だけ呼ぶと、つなぎ直しが要りません。"
 },
 "media_exists": {
  "why": "その添付の受け口は既に作られていて、同じものを 2 度は作りません。",
  "fix": "前に返った受け口の URL をそのまま使うか、thth_media_upload_url から新しく始めてください。",
  "better": "返った URL と media_id を控えておくと、作り直さずに続きから送れます。"
 },
 "media_expired": {
  "why": "その添付の受け口か公開の URL は、期限が切れたか取り消されて、もう使えません。",
  "fix": "thth_media_upload_url から新しく始め、返った新しい URL を使ってください。",
  "better": "添付は上げてから期限の内に原稿へ付けて出すと、出し直しが要りません。"
 },
 "media_internal": {
  "why": "添付の受け口の中で思わぬ失敗が起きました（受け取ったかどうかは分かりません）。",
  "fix": "同じ media_id は使わず、thth_media_upload_url から新しく始めてください。続くなら、命令と時刻を添えて `thth report file` で開発のセッションへ知らせてください。",
  "better": "起きた時刻と media_id を控えておくと、開発の側がすぐ原因を辿れます。"
 },
 "media_not_ready": {
  "why": "その添付は、まだ受け取りか検査を終えていません。",
  "fix": "受け口の URL への PUT を終えてから、thth_media_complete で完了させてください",
  "better": "添付を原稿より先に完了まで済ませると、下書きを置く手順が一度で済みます"
 },
 "media_size_exceeded": {
  "why": "添付の大きさが、その kind の上限（画像 8 MB・動画 1 GB・音声 100 MB）を超えています。",
  "fix": "上限より小さく書き出し直して、thth_media_upload_url から新しく始めてください",
  "better": "画像は長辺を媒体の表示に合わせて縮めると、見た目を保ったまま軽くなります"
 },
 "media_unavailable": {
  "why": "thth.me の添付の受け口が、いま答えを返せません。",
  "fix": "少し置いて、thth_media_upload_url から新しく始めてください。続くなら、命令と時刻を添えて `thth report file` で開発のセッションへ知らせてください。",
  "better": "添付は投稿の予定より早めに上げておくと、受け口が止まっていても間に合います。"
 },
 "media_upload_unconfirmed": {
  "why": "添付の送り込みが届いたかどうか、受け口の側で確かめられませんでした。",
  "fix": "同じ media_id は使わず、thth_media_upload_url から新しく送り直してください。",
  "better": "大きな動画は網の安定した所から送ると、途中で分からなくなることが減ります。"
 },
 "media_upload_unconfirmed_pending": {
  "why": "この添付には、届いたかどうか分からない送り込みが残っていて、続きを受け付けません。",
  "fix": "同じ media_id は使わず、thth_media_upload_url から新しく送り直してください。",
  "better": "送り込みは 1 つずつ終わりを待ってから次を送ると、分からない状態が残りません。"
 },
 "method_not_allowed": {
  "why": "その path は、送った HTTP のメソッド（GET・POST・PUT など）を受け付けません。",
  "fix": "URL を出した命令の出力どおりのメソッドで送り直してください（遠くの道の依頼は POST・結果は GET）。",
  "better": "HTTP を直接組まず thth の CLI か MCP の道具から呼ぶと、メソッドは道具がそろえます。"
 },
 "not_authorizing": {
  "why": "その招待は、媒体での許可を待っている状態ではありません。",
  "fix": "VM で `thth admin invite list` を見て、招待の今の状態から進め直してください。",
  "better": "招待の手順は 1 つの招待につき 1 回ずつ進めると、状態の食い違いが起きません。"
 },
 "not_clicked": {
  "why": "招待のページで、まだ「始める」が押されていません。",
  "fix": "招待された人に、招待のページで「始める」を押してもらってから、もう一度進めてください。",
  "better": "招待の URL を渡すときに押す所を一言添えると、待ちが短くなります。"
 },
 "not_found": {
  "why": "その URL の path か id は thth.me に無いか、期限が切れて消えました。",
  "fix": "URL を出した命令（`thth login`・招待・thth_media_upload_url など）をもう一度打ち、出た新しい URL を使ってください",
  "better": "URL は出た直後に使うと、期限が切れて消える前に済みます"
 },
 "not_reported": {
  "why": "そのアカウントの状態が、まだ VM から thth.me に届いていません。",
  "fix": "login の画面を開いたまま少し待ってください（届けば画面が自分で読み直します）。",
  "better": "招待で作ったばかりのアカウントは、VM の同期を 1 回待ってから login すると一度で通ります。"
 },
 "not_resettable": {
  "why": "その招待は、やり直しのできる状態（押された・許可を待っている）ではありません。",
  "fix": "VM で `thth admin invite list` を見て、使えない招待なら取り消して新しく作ってください。",
  "better": "やり直しは許可の途中で止まった招待にだけ使うと、済んだ招待を壊しません。"
 },
 "oauth_unavailable": {
  "why": "thth.me の認可の受け口が、いま答えを返せません。",
  "fix": "少し置いてから、同じ要求をもう一度送ってください。続くなら、命令と時刻を添えて `thth report file` で開発のセッションへ知らせてください。",
  "better": "AI の側は 1 回の失敗で登録をやり直さず、少し置いて同じ要求を送ると早くつながります。"
 },
 "person_exists": {
  "why": "そのアカウント名は、別の招待で既に使われています。",
  "fix": "別のアカウント名で招待をやり直してください。",
  "better": "アカウント名に project の名前を付けてそろえると、重なりにくくなります。"
 },
 "person_unavailable": {
  "why": "そのアカウントは、thth.me の側で今の版ではありません（作り直されたか、消えています）。",
  "fix": "VM の同期を 1 回待ってから、同じ操作をもう一度してください。続くなら、命令と時刻を添えて `thth report file` で開発のセッションへ知らせてください。",
  "better": "VM の同期を止めずに回しておくと、版の食い違いが残りません。"
 },
 "rate_limited": {
  "why": "thth.me の回数の上限（IP ごと・鍵ごと・受け口ごと）に達しました。",
  "fix": "応答の retry-after の秒数だけ待ってから、送り直してください",
  "better": "読むだけの確かめは 1 回取って使い回すと、回数を節約できます。"
 },
 "relay_unavailable": {
  "why": "thth.me の relay の受け口が、いま答えを返せません。",
  "fix": "少し置いてから、同じ命令をもう一度打ってください。",
  "better": null
 },
 "replayed_request": {
  "why": "同じ署名つきの依頼が 2 度届きました（使い済みの印か、時刻が 1 分よりずれています）。",
  "fix": "VM の時計を合わせてから、新しい依頼として同じ命令をもう一度打ってください。",
  "better": "VM の時計を NTP で合わせておくと、署名の時刻のずれで断られません。"
 },
 "reveal_unavailable": {
  "why": "アカウントのシークレットを、いま画面に出せませんでした（まだ出していません）。",
  "fix": "少し置いてから、招待のページの「表示する」をもう一度押してください。",
  "better": "表示されたシークレットはすぐパスワードの管理の道具に入れると、1 度きりの表示を逃しません。"
 },
 "secret_failed": {
  "why": "アカウント名かアカウントのシークレットが合いません（続けて違うと、しばらく受け付けません）。",
  "fix": "招待のときに 1 度だけ表示したシークレットを、写し間違えの無いように貼って送り直してください（失くしたときは https://thth.me/privacy/ の Contact から再発行を頼んでください）。",
  "better": "シークレットはパスワードの管理の道具に入れておくと、写し間違えも失くす心配も無くなります。"
 },
 "state_exists": {
  "why": "この認可の受け口は既に登録されています（同じ state を 2 度登録しました）。",
  "fix": "認可の命令を最初からやり直して、新しい state で始めてください。",
  "better": "認可は 1 つの state につき 1 回ずつ始めると、受け口が重なりません。"
 },
 "storage_unavailable": {
  "why": "thth.me の記録の置き場に、いま書き込めませんでした（何も変えていません）。",
  "fix": "少し置いてから、同じ操作をもう一度してください。続くなら、命令と時刻を添えて `thth report file` で開発のセッションへ知らせてください。",
  "better": "同じ操作を続けて何度も押さず 1 回ずつ待つと、記録の置き場が早く戻ります。"
 },
 "temporarily_unavailable": {
  "why": "thth.me の認可の受け口が、いま鍵か code を作れませんでした。何も許していません。",
  "fix": "少し置いてから、AI の側でコネクタの追加をもう一度始めてください。続くなら、命令と時刻を添えて `thth report file` で開発のセッションへ知らせてください。",
  "better": "同意の頁は開いたまま長く置かずに進めると、途中の期限切れが起きません。"
 },
 "too_large": {
  "why": "送った本文が、その受け口の上限（遠くの道は 48 KB）を超えています。",
  "fix": "本文を上限より小さくして送り直してください（長い原稿は分けて、1 回に 1 つずつ）。",
  "better": "長い本文はファイルにして thth の CLI から送ると、上限の前に道具が分けどころを知らせます。"
 },
 "too_many_actions": {
  "why": "アクティビティの画面から頼んだ操作が、まだ VM に渡っていないものだけで上限に達しました。",
  "fix": "VM が前の操作を拾うまで少し待ってから、もう一度押してください。",
  "better": "同じ取り消しは 1 回だけ押して画面の表示が変わるのを待つと、操作が重なりません。"
 },
 "too_many_codes": {
  "why": "使われずに残っている認可の code が上限に達しています。",
  "fix": "1 分ほど置いてから（code は 60 秒で消えます）、同意の頁からやり直してください。",
  "better": "同意の頁で許したら、AI の側の戻り先をそのまま開いて code を使い切ると、上限に届きません。"
 },
 "too_many_connectors": {
  "why": "thth.me が預かれるコネクタの数が上限に達しています。",
  "fix": "https://thth.me/connectors で要らないコネクタを取り消してから、もう一度許してください。",
  "better": "使わなくなったコネクタは /connectors で取り消しておくと、VM の鍵も一緒に止まります。"
 },
 "too_many_requests": {
  "why": "同じ鍵の前の依頼がまだ終わっておらず、待っている依頼が上限に達しました。",
  "fix": "前の依頼の結果を /api/v1/result/<request_id> で受け取ってから、送り直してください",
  "better": "続けて打つ命令は前の結果を待ってから順に打つと、待ちが一番短く済みます。"
 },
 "unknown_client": {
  "why": "その client_id は登録が無いか、登録の期限が切れています。",
  "fix": "AI の側からコネクタの追加をやり直してください（登録からやり直します）。",
  "better": "client の身元を https の URL の文書（CIMD）で示すと、登録の期限を気にせずつながります。"
 },
 "webhook_capacity": {
  "why": "Webhook の通知の控えが上限に達していて、新しい通知を置けません。",
  "fix": "VM で `thth webhook pull` を打って控えを読み進めてから、もう一度送ってください。",
  "better": "VM の `thth worker` を止めずに回しておくと（30 秒ごとに取り込む）、控えが上限まで溜まりません。"
 },
 "webhook_unavailable": {
  "why": "thth.me の Webhook の受け口が、いま答えを返せません。",
  "fix": "少し置いてから、もう一度送ってください。続くなら、命令と時刻を添えて `thth report file` で開発のセッションへ知らせてください。",
  "better": "取り込めなかった時間は VM の `thth webhook status` で見ると、取りこぼしが分かります。"
 },
 "workspace_mismatch": {
  "why": "コネクタが結ばれた括りと、VM が答えた括りの名が違います。答えの中身は出していません。",
  "fix": "https://thth.me/connectors でこのコネクタを取り消し、同意の頁で括りを名指しして許し直してください",
  "better": "括りが 2 つ以上あるときは `--workspace <括り>` で名指しして login すると、取り違えが起きません。"
 }
});
