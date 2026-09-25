# 下書き: 利用規約と privacy の 3.12.0 改訂（2026-09-26）

状態: **下書き・masaru の確認待ち**。`callback/public/terms/`・`callback/public/privacy/` はまだ変えていない（Worker の deploy で公開されるため）。確認が取れたら、施行日を決めて本体に入れる。

## なぜ要るか

設計 3.12.0（`docs/設計_3.12.0_承認を関所から選ぶものへ_2026-09-26.md`）で、招待の口座は既定で承認ページを通さず、持ち主が自分の AI アシスタントに頼めば出るようになった。今の利用規約と privacy は「毎回本人が承認する」と約束しているので、そのままでは実態と食い違う。

食い違う箇所:

- 利用規約 "What THTH is": "drafting, approving and publishing"
- 利用規約 "What you do": "Approve each post yourself. Drafts may be written with an AI model; read each one before you approve it." / "Approve only content that does not infringe…"
- 利用規約 "What the operator does and does not do": "THTH does not publish a post that has not been approved. …"
- privacy（39 行目あたり）: "…and later approves each post on a thth.me approval page."
- privacy に `/activity`（持ち主の動きの一覧）と、LLM の鍵の発行の記述が無い（段 3 の実装が決まったら、Worker に何をどれだけ残すかを書く）。
- `callback/public/llms.txt` の冒頭 "THTH is a human-approval gate for LLM-drafted public posts" と `callback/public/index.html` の説明（公開の自己紹介。変えるかどうかも masaru の判断）。

## 利用規約の差し替え案（英語・日本語の節も同じ筋で）

**What THTH is**

> THTH is a tool for publishing and managing social media posts on your own accounts, usually through your own AI assistant. The operator, gotoq, runs it on an operator-managed server for people the operator has invited or otherwise verified. Invited users connect their own accounts and do not run their own server or register their own app. These terms cover that service.

**What you do**

> - Authorize THTH only for accounts you control, on the platform's own screen, after checking the account and the permissions it asks for.
> - You are responsible for what is published, replied to or deleted on your accounts, whether you ask for it yourself or through an AI assistant you connect. If you want to read each post before it goes out, turn on approval for your account.
> - Follow the terms and rules of each platform you post to.
> - Publish only content that does not infringe other people's rights, such as copyright, privacy or portrait rights.
> - Keep your approval secret, your assistant key and your platform credentials to yourself. Do not paste them into chats, drafts or issues. If you think a key has leaked, issue a new one on https://thth.me/activity.

**What the operator does and does not do**（1 項目めを差し替え）

> - THTH acts only when you ask for it, through your assistant or on a thth.me page; it does not post, reply or delete on its own. Every account has safety limits you control (minimum interval, daily caps, a stop on bursts of actions); an assistant can only tighten them. If you turn on approval, THTH does not publish or delete without it, and changing the approved content requires approval again.

## privacy の差し替え案（39 行目の文）

> …Either way, the user reviews the account and permissions on the platform and approves. Afterwards the user publishes through their own AI assistant, or, if they turn on approval, approves each post on a thth.me approval page.

（`/activity` と鍵の発行の節は段 3 の実装を見てから足す。）

## 施行日

改訂は利用者に不利になる変更ではない（できることが増え、止める手段が増える）が、約束の中身が変わるので、施行日を新しくし、招待済みの人（今は審査用のテスト口座だけ）に知らせる。
