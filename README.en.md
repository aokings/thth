# THTH (ThreadsThrower)

THTH posts approved drafts to Threads (Bluesky and Mastodon adapters exist but are
not yet running in production). It does not decide what to write.

**What it guarantees**
- Approval is a human act: a post only ships once a person runs `thth approve`
  a second time with the digest the first run showed them, and `status: approved`
  plus that digest land in git.
- Fail-closed, at least three ways: an unfinished previous post (`inflight`) halts
  the account instead of retrying; a queue file whose committed content doesn't
  match the working tree (`unverified_content`) is refused, not posted; a file
  edited after approval (`approval_stale`) is refused until re-approved.
- One post per account per run (`max_per_run`, default 1); a timer tick just
  runs that loop again.
- Every public act is recorded in your git history: the queue file, the approval
  commit, the `post_id` written back, the collected replies and metrics.

**What it refuses**
- No post without `status: approved` and a matching digest — editing the body,
  account, topic, reply target, or scheduled time after approval invalidates it.
- No post inside an account's configured quiet hours (`quiet_hours`), when set.
- No duplicate posts — an in-flight marker blocks the next run until a human
  confirms what actually happened.
- No tokens or secrets in logs — access tokens, client secrets, auth codes, and
  Authorization headers are redacted before anything is written or printed.

**What it never does**
- Never sends anything to a third party. There is no data-sharing feature today;
  the design for one (`thth share`, opt-in, default off) is written down but not
  built yet — see [docs/設計_v2_泉と門_2026-09-13.md](docs/設計_v2_泉と門_2026-09-13.md).
- Never reads direct messages. It only ever touches public posts and public replies.
- Never auto-replies with canned text. Reply drafts go through the same
  human-approval path as any other post.

---

## Install

There is no PyPI package yet (packaging is planned, not published). Today:

```bash
git clone <this repo>
cd thth
python -m thth --version   # or: put bin/thth on your PATH
```

Authorizing an account (one line each):

- **Threads**: `thth auth <account>` — walks you through the OAuth code exchange
  for your own Meta app. First-time setup (creating that app) is
  [docs/導入_自分のMetaアプリで動かす.md](docs/導入_自分のMetaアプリで動かす.md).
- **Bluesky**: `thth auth <account>` — prompts for your handle and an App
  Password. Setup: [docs/導入_Bluesky_2026-09-13.md](docs/導入_Bluesky_2026-09-13.md).
- **Mastodon**: `thth token set <account>` — paste an access token issued by
  your instance. Setup: [docs/導入_Mastodon_2026-09-13.md](docs/導入_Mastodon_2026-09-13.md).

## Daily flow

1. An agent (or a person) drafts a post as a queue file
   (`docs/sns/queue/<date>-<slug>.md`, `status: draft`), then commits and pushes it.
2. A human approves it: `thth approve <file>` shows the full text and a digest
   (first pass, no write); the human says yes, and `thth approve <file> --confirm
   <digest> --by "<name>"` writes `status: approved` and pushes.
3. A timer (10-minute tick) posts the next due, approved file and writes the
   `post_id` back.
4. Replies and metrics are collected on a schedule (1/6/24/72/168 hours after
   posting) into your repo, where you can read them with `thth replies` and
   `thth measured`.

Full walkthrough: [docs/usage.en.md](docs/usage.en.md).

## Commands

All 25 subcommands `thth --help` lists today, one line each:

- `lint` — check a queue file's front matter and length
- `preview` — show the exact text that would go out
- `approve` — two-step approval (show, then confirm with a digest)
- `account` — one account's postable status
- `revoke` — undo an approval and return the file to `draft`
- `posts` — list posts actually made, including ones sent outside THTH
- `replies` — read the collected-reply ledger
- `measured` — read the collected-metrics ledger
- `threads` — thread shape: branches, depth, participants, time to first reply
- `topics` — how much each topic was seen
- `forms` — vocabulary and guidance for post shapes (no measurement yet)
- `queue` — draft/approved/posted counts and what's next
- `schedule` — what's due, in date order
- `throw` — post one approved file (dry-run by default)
- `run` — throw + collect + refresh, in one call (what the timer runs)
- `systemd` — generate a `.timer`/`.service` unit from the account config
- `board` — freshness, in-flight state, and malformed files per account
- `collect` — gather metrics and replies on the elapsed-time schedule
- `auth` — exchange an authorization code or credentials for a long-lived token
- `refresh` — refresh a long-lived token
- `maintain` — keep every account's token alive (independent of posting)
- `send` — post one text immediately, bypassing the queue (in-person approval)
- `doctor` — read-only capability check for a token
- `app` — store or show the local Meta app config
- `token` — set a long-lived token directly (paste-in, e.g. Mastodon)

`thth ask` (a query-before-you-post advisor) and `thth share` (opt-in metrics
sharing) are designed but not implemented — see
[docs/設計_v2_泉と門_2026-09-13.md](docs/設計_v2_泉と門_2026-09-13.md) §1 and §3.

## What's not here

- No dashboard.
- No bulk scheduling UI — one file per post, plain text, in your own repo.
- No autopilot — nothing posts without a human's explicit approval.

## License

MIT, per the September 2026 design ruling
([docs/設計_v2_泉と門_2026-09-13.md](docs/設計_v2_泉と門_2026-09-13.md) §7-2). The
`LICENSE` file and the switch to a public repo are still pending, done by hand
by the maintainer.
