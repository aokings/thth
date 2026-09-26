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

## Version 2.7.0

Adds the 720-hour (30-day) insights mark; the metrics collection window is now derived from the marks (38 days) while the reply window stays independent. New observations carry the most recent daily follower count from the previous 48 hours as context (`followers_count_at`, `staleness_hours`, or a `context_reason` when none); no extra API calls, no backfilling of existing rows. Collection marks are aligned across Threads, Bluesky and Mastodon; metrics a medium does not have stay `null`. Real 30-day collection against live APIs is not yet observed.

## Version 2.6.0

Analytics polish using existing ledgers only: per-mark values (1h/6h/24h/72h/168h/720h bands), IQR/min/max next to every median, outcomes per engaged thread, collection success/attempt freshness, stratified period comparisons (`--by kind|hour_band|topic`), thread shapes bounded by collection time, `cannot_say_details` codes beside the existing prose, an eligibility forecast for immature study posts, and `handoff-report --since-last-read` (a cursor is written only with an explicit `--mark-read --by NAME`; MCP and HTTP stay read-only). Existing keys are unchanged and `schema_version` stays 1. Independently audited and fixed before release.

## Version 2.5.0

Adds three read-only reports — `analytics-report` (activity snapshot, `--compare-previous` for adjacent-period comparison), `handoff-report` (local operations evidence for a session handoff) and `study-report` (links a declared study to the owner's own observations) — plus the MCP tools `analytics_report`, `operations_handoff` and `study_report`. Numbers always carry their period, sample size, missing data and evidence; no causal claims, no recommendations. `serve-reports` is a development-grade private report transport (Unix socket by default, service credentials, read-only) for a dedicated Unix environment; it is not a public server and includes no human login or TLS. A pure offline normalizer for X owned public metrics is included but not connected to any API. Independently audited (4×P2, 7×P3) and fixed before release.

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

### Operator setup and invited users

Masaru runs the server and the developer apps. Invited users open the authorization
URL and approve their own SNS account; they do not create a VM, a Meta app, or a
THTH account ledger. The connection page is future work. Version 2.11 implements
the operator CLI and temporary callback relay, not that page.

The operator creates ledgers outside this repo under
`$THTH_ROOT/accounts/<account>.json`, with `account add --by masaru`; new ledgers
have `production: false` and `scheduled: false`. Fill the real handle, instance
where applicable, and registered callback `https://thth.me/callback/`. The
operator stores Threads/X clients using `app set <medium> --stdin --by masaru`.
Client JSON comes from a secret manager, never from command arguments or a chat.
Mastodon registers automatically only when its metadata confirms the required
PKCE and scope capabilities. App events record presence only, including client ID.

- **Threads / Mastodon / X**: `thth auth <account> --by masaru`. Open the human URL,
  approve, and let the server receive the code. Paste fallback remains available.
  X is **authorization only**; posting and collection remain unsupported.
- **Bluesky**: pipe an App Password from a secret manager into
  `thth token set <account> --stdin --by masaru`. This is not atproto OAuth.
- **Threads / Mastodon escape hatch**: the same `token set --stdin` accepts an
  access token, with identity validation. Existing credentials require `--force`.
  `op read` is one possible source; the 1Password CLI is not bundled.

`doctor` keeps authorization history separate from explicit probe timestamps.
Response scopes take priority over probe inference. X expiry is response-derived;
the unchanged daily timer cannot guarantee timely refresh of a short-lived X
token. Keeping old local bytes after a failed rotation does not guarantee the
old remote refresh token still works; reauthorize when necessary.
See the [unified guide](docs/導入_承認を押すだけ.md) for operator preparation,
private JSON schemas, failure semantics, and unverified deployment/API limits.

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

- `admin` — authenticated read-only inventory, account, log, tokens, timers, release and diff reports.

All subcommands `thth --help` lists in this development checkout, one line each:

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
- `serve-reports` — development-grade (since 2.5.0): private read-only HTTP reports over a Unix socket (or explicitly selected loopback TCP) for a dedicated Unix environment; see the [setup and limitations](docs/非公開レポートHTTP_v1.md).
- `worker` — operator-run daemon: finishes invitations, pushes `/activity` summaries and carries out the operations requested there (safety controls, keys); see the [server write setup](docs/運用_サーバ書込_2.12.md). Existing read-only credentials stay read-only. `approval-worker` is the deprecated old name (renamed in 3.13.0).
- `handoff-report` — since 2.5.0: local operations evidence, pending notifications and explicit freshness limits.
- `observe` — since 3.3.0: the same page as `morning`, meant for the start of a session and every checkpoint; section 2 covers the time since your last observation (or yesterday in JST when there is none or it is older than 7 days). `morning` stays as an alias.
- `morning` — since 3.1.0: one morning page for a project or account — tool version, unanswered replies and mentions, yesterday's own posts, the world around your admin-set watch words, today's plan and budget, and candidate next steps. Read-only; it never drafts text.
- `report` — since 3.1.2 (development): file a bug or a request inside the tool (`report file <account> --kind bug|request`), read your project's reports and the implementers' replies (`report list`, `report show`). Refused if the text looks like a secret.
- `study` — add an explicit post ID or queue post ID to a local study JSON with `--by`; no adoption or git commit.
- `unanswered` — list locally evidenced unanswered replies to this account's root posts, with freshness and uncertainty.
- `study-report` — since 2.5.0: link an unverified study declaration to explicitly selected owned root-post observations; read-only, no causal-effect claim. See the [contract](docs/施策レポート_v1.md).
- `analytics-report` — since 2.5.0: read-only activity snapshot with evidence, missing data, JSON and Markdown.
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
- `inflight` — see an unresolved publish (`show`) and, after checking the
  platform yourself, resolve it (`resolve --not-published` or
  `--published <post_id>`; two-step confirmation; a copy and a change-log entry
  are kept; not exposed over MCP)
- `location` — search for a place (`thth location search <account> <word>`,
  read-only)
- `plaza` — the measures plaza: share what you tried and how it went across the
  media of the same owner (measures get observations computed by the tool;
  findings and questions; replies including re-trials that did or did not
  reproduce). Other owners see an entry only when it is marked open and both
  owners have joined; other people's text, usernames and author keys are
  dropped first
- `map` — the observation map: topics (points) chosen by a person and how they
  nest (lines), with your own posts' numbers and the plaza entries laid on the
  same points (`thth map show <project>`, read-only). The world layer (daily
  search counts) is off by default and stays inside the project

## What's not here

- No dashboard.
- No bulk scheduling UI — one file per post, plain text, in your own repo.
- No autopilot — nothing posts without a human's explicit approval.

## License

MIT, per the September 2026 design ruling
([docs/設計_v2_泉と門_2026-09-13.md](docs/設計_v2_泉と門_2026-09-13.md) §7-2). The
`LICENSE` file and the switch to a public repo are still pending, done by hand
by the maintainer.

## v3 foundation (since 2.5.0)

`analytics-report` reads local ledgers and returns a snapshot with period, sample sizes, missing data, and evidence. See the [schema and usage](docs/分析レポート_v1.md).

`handoff-report` / MCP `operations_handoff` reads local operations evidence without syncing or retrying. See the [schema and limits](docs/運用引継ぎレポート_v1.md).

Account creation/overwrite, auth, token set and local token revoke require `--by <name>` as of 2.9.0. See [the administrator skill](skills/thth-admin/SKILL.md) for the six credential-gated MCP reports and HTTP admin scope.

## Local 2.12 candidate

Server write requests, resumable account leave, and an administrator X read budget are implemented locally; this is not a deployment or production acceptance claim. X auth/refresh identity reads require a budget before token exchange (default USD 0). See [budget operations](docs/運用_X読取予算_2.12.md). X posting/collection adapters remain unsupported.
