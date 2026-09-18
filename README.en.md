# THTH (ThreadsThrower)

THTH posts approved drafts to Threads, Bluesky and Mastodon. It does not decide
what to write.

**Where to start** (the same three lines `thth --help` opens with)
- Post one draft, once → `thth send` (a rehearsal by default; nothing goes out
  until you add `--production`)
- Run an account from a queue → `thth lint` → `thth approve` (two steps) → `thth throw`
- Ask before you post → `thth ask before-you-post`

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
- Read-only lookups (`thth where`, `thth thread`, `thth who`) do not persist
  fetched post bodies; minimal execution metadata is logged. The engagement ledger
  keeps your own acts and reactions. Configured monitoring
  services and email recipients receive operational status, not post bodies.
- Never reads direct messages. It only ever touches public posts and public replies.
- Never auto-replies with canned text. Reply drafts go through the same
  human-approval path as any other post.

---

## Version 2.4.0

Adds per-account user and administrator incident emails, recovery notices, and
operational fields in the original queue file. Configure external missed-ping
monitoring for VM or process outages. Notification settings and secrets stay
outside Git. Threads posting failures retain bounded, allowlisted API diagnostics.
`after` now includes owned posts, project scope, and topic-kind summaries.
See [notification setup](docs/停止通知と運用記録.md).

## Install

```bash
pip install thth
thth --version
```

Zero runtime dependencies; `thth` and `thth-mcp` entry points. Released on
[PyPI](https://pypi.org/project/thth/) and listed in the
[MCP registry](https://registry.modelcontextprotocol.io/?q=io.github.aokings/thth)
as `io.github.aokings/thth`. From source: `git clone`, then `python -m thth --version`
or put `bin/thth` on your PATH.

### Where your account ledgers live

One JSON file per account, **outside this repo**:
`$THTH_ROOT/accounts/<account>.json` (override with `$THTH_ACCOUNTS_DIR`).
Nothing you configure is committed here. **A fresh clone ships no ledgers at
all** (the in-repo `accounts/` directory was removed on 2026-09-14); you start
by writing your own:

```bash
thth account add your-project-threads --media threads --project your-project \
  --redirect-uri https://your.domain/callback/
```

writes one from the bundled template (`accounts.example/<media>.json`) with
`production: false` and `scheduled: false` — it will **not** post until you
edit those by hand. Bluesky and Mastodon require `--handle` (and Mastodon
`--instance`), because the default cannot match there. Any template
placeholder you leave behind is named by `account add`, by `thth doctor`, and
by `thth auth`, which refuses before it prints an authorization URL. If you are upgrading from a version that kept ledgers in
the repo's own `accounts/` directory, `thth account migrate` copies them out
(copy, never move; it refuses to overwrite anything that differs). That old
location is still read for one release, with a warning.

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
   `thth measured`. Posts made in person with `thth send` are collected the
   same way (they carry `source: "sent"`); an account with no repo keeps those
   ledgers under `$THTH_ROOT/state/<account>/data/sns/`.

Full walkthrough: [docs/usage.en.md](docs/usage.en.md).

## Commands

All 39 subcommands `thth --help` lists in this development checkout, one line each:

- `lint` — check a queue file's front matter and length
- `preview` — show the exact text that would go out
- `approve` — two-step approval (show, then confirm with a digest)
- `account` — one account's postable status; `account add <name> --media
  threads|bluesky|mastodon --project <p>` writes a new ledger from the bundled
  template (`--redirect-uri` on Threads; `--handle`, and `--instance` on
  Mastodon, are required where the default cannot match), and `account migrate`
  copies ledgers out of an old in-repo `accounts/` directory (see "Where your
  account ledgers live" below)
- `revoke` — undo an approval and return the file to `draft`
- `posts` — list posts actually made, including ones sent outside THTH
- `replies` — read the collected-reply ledger
- `measured` — read the collected-metrics ledger
- `threads` — thread shape: branches, depth, participants, time to first reply
- `handoff-report` — in development, not in published 2.4.0: local operations evidence, pending notifications and explicit freshness limits.
- `study-report` — in development, not in published 2.4.0: link an unverified study declaration to explicitly selected owned root-post observations; read-only, no causal-effect claim. See the [contract](docs/施策レポート_v1.md).
- `analytics-report` — in development, not in published 2.4.0: read-only activity snapshot with evidence, missing data, JSON and Markdown.
- `after` — called after posting: how the replies you went and engaged in were
  received, with counts and timing (read-only)
- `topics` — how much each topic was seen
- `forms` — vocabulary and guidance for post shapes (no measurement yet)
- `queue` — draft/approved/posted counts and what's next
- `schedule` — what's due, in date order
- `throw` — post one approved file (dry-run by default)
- `run` — throw + collect + refresh, in one call (what the timer runs)
- `systemd` — generate a `.timer`/`.service` unit from the account config
- `board` — freshness, in-flight state, and malformed files per account
- `collect` — gather metrics and replies on the elapsed-time schedule, for
  queued posts **and** for posts made with `send`; with no repo configured the
  ledgers go to `$THTH_ROOT/state/<account>/data/sns/` instead of your repo
- `pull` — explicitly fetch and fast-forward an account's repo (or every repo
  under a `--project`), the same `sync_repo()` call `approve` already makes;
  read-only commands (`queue`/`schedule`/`board`) never pull on their own
- `auth` — exchange an authorization code or credentials for a long-lived token
- `refresh` — refresh a long-lived token
- `maintain` — keep every account's token alive (independent of posting)
- `notifications` — configure and test user/admin incident emails, and inspect pending notifications (development main)
- `send` — post one text immediately, bypassing the queue (in-person approval)
- `doctor` — read-only capability check for a token
- `app` — store or show the local Meta app config
- `token` — set a long-lived token directly (paste-in, e.g. Mastodon)

- `ask` — `before-you-post`: what happened last time you used this word, at this
  hour, in this shape.

```bash
thth ask before-you-post <your-account> --topic <word> [--kind …] \
  [--hour-band 朝|昼|夕|深夜] [--reply] [--window-days 30] [--min-n 20] [--json]
```

It answers from **your own local ledgers only** — the replies and metrics THTH
already collected into your repo. It is **not** the spring (`thth-spring`, the
shared pool described in
[docs/設計_v2_泉と門_2026-09-13.md](docs/設計_v2_泉と門_2026-09-13.md) §1); no
network call is made and `provenance.source` says `"local"`. It never sees your
draft — you pass a word, a shape and an hour band, never the body.

Expect **mostly `cannot_say`** at first: medians are withheld below `--min-n`
(default 20) comparable posts inside `--window-days` (default 30), and one
account's first weeks rarely reach that. That is the intended answer, not an
error — rc stays 0, and `cannot_say` names each reason with its `n`.

The remaining 7 subcommands, one line each:

- `mentions` — list mentions of your account (Threads, read-only,
  `threads_manage_mentions`)
- `profile` — look up a public profile (Threads, read-only,
  `threads_profile_discovery`)
- `thread` — read a post's branch on the spot; nothing is saved
- `where` — where to go engage next: search results with your own history
  layered on top (read-only)
- `who` — pseudonymous history: how often you've crossed paths, when, and how
  it went; it never holds what was said (read-only)
- `retract` — take down a published post (two-step confirmation; the record is
  kept, not deleted; Threads only)
- `location` — search for a place (`thth location search <account> <word>`,
  read-only)

## What's not here

- No dashboard.
- No bulk scheduling UI — one file per post, plain text, in your own repo.
- No autopilot — nothing posts without a human's explicit approval.

## License

MIT, per the September 2026 design ruling
([docs/設計_v2_泉と門_2026-09-13.md](docs/設計_v2_泉と門_2026-09-13.md) §7-2). The
`LICENSE` file and the switch to a public repo are still pending, done by hand
by the maintainer.

## In development (v3 foundation, unreleased)

`analytics-report` reads local ledgers and returns a snapshot with period, sample sizes, missing data, and evidence. It is not included in published 2.4.0. See the [schema and usage](docs/分析レポート_v1.md).

The development-only `handoff-report` / MCP `operations_handoff` reads local operations evidence without syncing or retrying. See the [schema and limits](docs/運用引継ぎレポート_v1.md).
