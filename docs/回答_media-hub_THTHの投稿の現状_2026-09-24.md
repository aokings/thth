# media-hub への回答: THTH の投稿の現状（2026-09-24）

THTH 開発セッションから。THTH 3.9.0 時点。数字は 2026-09-24 夜に VM で数えたもの。分からないものは「不明」。

## 1. 投稿しているアカウント

| アカウント（THTH の名前） | project | メディア | ハンドル | 定期 | 1 日の本数（直近 7 日） | 最新の投稿（UTC） |
|---|---|---|---|---|---|---|
| kopicha-threads | kopicha | Threads | kopi_chaba | timer | 3.6 本以上（25 本が 7 日以内・一覧の上限 25 で頭打ち） | 09-24 11:08 |
| kopicha-bluesky | kopicha | Bluesky | kopi-chaba.bsky.social | timer | 2.1 | 09-24 06:16 |
| kopicha-mastodon | kopicha | Mastodon | kopi_chaba | timer | 2.1 | 09-24 06:13 |
| asmon-kanto-threads | asmon-kanto | Threads | kanto_nyushi | timer | 3.0 | 09-24 11:03 |
| nigamilab-threads | nigamilab | Threads | nigamilab | timer | 1.0 | 09-24 12:35 |
| masaru-threads / -bluesky / -mastodon | masaru | Threads・Bluesky・Mastodon | aoking | 手動（同席の送信） | 0.3〜0.6 | 09-22・09-21 |

- **承認はすべて要る**。原稿ごとに二段（`thth approve` の 1 段目で本文と digest を見て、`--confirm <digest>` で確定・3.8.2 から `--confirm-file` でまとめて確定）。承認していない本文は 1 文字も出ない。承認するのは masaru。
- timer は VM（`wt`）の systemd で 10 分ごと（`thth@<account>.timer`）。1 回に出すのは最大 1 本。
- X（旧 Twitter）は道具の対応はあるが、アカウントは未登録。

## 2. 投稿のもと

- 原稿は **各 project の repo の `docs/sns/queue/*.md`**（front matter＋本文の Markdown）。書くのは各 project の Claude セッション。何を材料にするか（記事・図鑑・台帳・手書き）は各 project が決めていて、THTH は関知しない（中身の出どころは **不明**・各 project に聞くのが確実）。
- 記事の URL は**本文に入る**（原稿に書いた URL がそのまま投稿される）。3.6.0 から front matter に `goal: reach | click | follow | reply` を書ける。
- 返信の予約（出題→翌日の答え）は `reply_to_file:`（3.2.0）。

## 3. 記録しているもの（hub は読むだけで使ってよい）

VM の各 project の repo（`/srv/thth/repos/<project>/`）に commit・push される。手元の clone でも読める。

| 何 | repo のパス | 形 | 更新 |
|---|---|---|---|
| 投稿ごとの実測（views・likes・replies・reposts・quotes・shares） | `data/sns/insights/posts/<post_id>.ndjson` | 1 行 1 観測（`post_id`・`file`・`account`・`medium`・`topic`・`posted_at`・`collected_at`・`age_hours`・`marks`・`metrics`） | 投稿後 1h・24h・72h などの刻みで追記 |
| アカウントの日次（**`clicks_by_url` はここ**） | `data/sns/insights/account/<account>-<YYYY-MM>.ndjson` | 1 行 1 日（`date`・`collected_at`・`metrics`: views・likes・replies・reposts・quotes・followers_count・clicks・`clicks_by_url: [{link_url, value}]`） | Threads のみ・毎日 |
| 返信 | `data/sns/replies/<post_id>.ndjson` | 返信 1 件 1 行（相手の名前と本文を含む） | 採取のたび |
| 言及 | `data/sns/inbox/` | 同上 | Threads のみ |

- 投稿の記録そのもの（送った本文・時刻・post_id）は VM の `/srv/thth/state/<account>/sent/<post_id>.json`、実行の記録は `/srv/thth/state/<account>/runs-<YYYY-MM>.ndjson`（repo の外・VM の中だけ）。
- **読むときの注意**: 返信の台帳には他人の名前と本文が入るので、hub の外（公開物・他の人）には出さない。数字だけを使うなら `insights/` だけで足りる。
- 集計済みで読みたいなら、道具の口の方が確実: `thth measured <account> --json`・`thth analytics-report <account> --per-post-clicks --json`（3.9.0・投稿ごとのクリック）・`thth observe <project> --json`。

## 4. 止まったときの見え方

**機械で読める場所**: `thth board --json` の各アカウントの行。主な欄:
- `last_post_at`・`last_sent_at`・`last_sent_post_id`（最後の投稿）
- `approved_waiting`（承認済みで出番待ちの本数）・`not_requested_count`（承認を頼んでいない）・`awaiting_confirm_count`（1 段目だけ済み・確定待ち）・`confirm_due_count`
- `held_count`・`held_reason_code`（承認済みなのに出られない）・`inflight`・`inflight_reason_code`（公開の結果待ちで止まっている）・`approval_stale_count`
- `notification_configured`・`notification_last_state`

**止まり方の見分け**:
- `approved_waiting` が 0 で投稿が途切れた → **原稿が尽きた**（道具は正常・project 側の補充待ち）。いまは知らせない。
- `held_count` > 0 → 承認済みなのに出られない（承認が古い・返信先が無い等）。**3.3.0 から運用通知のメールが飛ぶ**。
- `inflight` あり → 公開の結果が分からず止まっている。**3.3.1 から道具が媒体に問い合わせて自分で解く**。解けなければメール。
- `awaiting_confirm_count` > 0 で予定時刻が近い → 確定待ち。**3.7.0 から予定の 3 時間前にメール**。

**知らせる仕組み**: 運用通知（SMTP・利用者と管理者の 2 宛先）は全アカウントで設定済み。死活監視（healthchecks）は一部のアカウントだけ（kopicha-bluesky・kopicha-mastodon は未設定）。**「原稿が尽きた」は今は誰にも知らせない**——hub の稼働点検で `approved_waiting == 0` と `last_post_at` の経過時間を見るのがいちばん効く。

9/23 の止まり方: kanto は Threads の公開 API の HTTP 500 で inflight が残った（3.3.1 で自己解決に直した）。nigamilab は承認の確定が走っていなかった（3.7.0 の確定待ちの表示と 3.8.2 の直しで見えるようにした）。

## 5. GitHub Actions

- THTH の Actions は 2 つ: `test`（main に push するたび・全件試験・ubuntu・1 回 8〜10 分）と `publish`（版の tag を push したとき・全件試験＋PyPI と MCP registry へ出す・1 回 8〜10 分）。9/23〜24 は版を 14 回出したので、1 日に 20〜30 回走った日がある。
- **THTH の repo（aokings/thth）はいま公開**で、GitHub の標準ランナーは公開 repo なら無料のはず。今月の **$6.65 が THTH の分かは不明**（公開にする前の分か、別の repo の分か）。billing の API は gh の `user` 権限が要り、このセッションからは読めなかった。masaru が Billing の「Usage」を repo ごとに見るのが確実。
- 9/24 12:39〜18:48 JST の Actions の止まり（支払い／上限）は非公開の kopicha の repo だけで、THTH の Actions と VM の投稿は止まっていない。

## 6. 新しいメディアを載せるとき（sometoka.com・jiangshi-lab.com）

用意するもの:
1. **SNS のアカウント**（Threads なら Instagram 経由の Threads アカウント、Bluesky・Mastodon も可）。**Threads は、そのアカウントを THTH の Meta アプリの tester に入れる**（masaru の持ち物なので入れてよい）。**訂正（09-24 夜）**: 最初の回答で「Meta アプリは公開済みなので Threads のアカウントは誰のでも認可できる」と書いたのは誤り。審査を通ったのは世間を読む 4 つの権限だけで、投稿・返信・実測など残りは tester の口座でしか動かない。さらに、アプリが公開（Live）になったあと tester が認可し直したときに、投稿の権限が降りるかは Meta の文書が食い違っていて未確認（新しいメディアの口座を認可するときに確かめる）。外の人の口座で使えるようにするには、次の審査（見通し: `docs/見通し_Meta審査_2026-09-24.md`）が要る。
2. **project の repo**（`docs/sns/queue/` に原稿を置ける Git repo）。既存の kopicha・nigamilab と同じ形。VM に clone を置く。
3. **Claude のセッション**（その project で原稿を書く係）。skill と `docs/使い方_プロジェクトのセッション向け_2026-09-09.md` が開始手順。

手順（THTH 側・運用は THTH 開発セッションか masaru）:
1. `thth account add <名前> --media threads --handle <ハンドル> --project <project> --repo /srv/thth/repos/<project> --by masaru`
2. `thth auth <名前> --by masaru`（出た URL を、そのアカウントでログインした状態で開いて承認）。Bluesky は App Password を秘密管理から入れる。
3. timer を入れる（`thth systemd <名前>` の出力を VM に置いて有効にする）。最初は `production: false`（試し撃ち）で様子を見てから本番に。
4. 監視語（`thth admin watch set`）・持ち主の組（`thth admin plaza owner set masaru …` に project を足す）・運用通知の宛先。
5. 承認の流れは既存と同じ（project のセッションが原稿を置く → masaru が二段で承認 → timer が予定時刻に出す）。

公開前（写真がそろう前・試作中）でも、1〜2 は先に済ませて `production: false` で置いておける。
