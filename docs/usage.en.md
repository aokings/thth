# THTH usage guide (English summary)

This is a summary of
[使い方_プロジェクトのセッション向け_2026-09-09.md](使い方_プロジェクトのセッション向け_2026-09-09.md)
(§2–§8) and
[引継ぎ_トピックの棚_アカウント側セッションへ_2026-09-12.md](引継ぎ_トピックの棚_アカウント側セッションへ_2026-09-12.md)
(§2). Where the two disagree, the Japanese original is the source of truth —
it is the operational document, updated as reality changes. Examples below
use `your-account` in place of any real account name.

## 1. What it does, what it doesn't

THTH takes a body of text and posts it: it writes the `post_id` back, and
collects replies and metrics into your repo. It does **not** decide what to
write, how often, when, or which topic to use — that is entirely your
project's call. It sends your text as given, without reformatting or
truncating it. Platform caps (e.g. Threads' 250 posts / 1,000 replies per 24h)
are not enforced by THTH itself.

Setting up a new account? Read §9 (where the account ledger file lives) first —
it is not in this repo. §10 is `thth ask before-you-post`, §11 is `thth share`.

## 2. Posting in person (`thth send`)

When a human is present and says "post this," you can skip the queue file:

```bash
thth send <your-account> --text-file /tmp/post.txt
```

- Nothing goes out until you add `--production`. Without it, THTH prints the
  text it would post and a short digest (`digest: ab12cd34ef56`).
- To actually post, pass that digest back: `--production --confirm
  ab12cd34ef56`. A missing or mismatched digest is refused — this is the
  machine's proof that what was shown and what is sent are the same text.
- `--topic <word>` sets a topic (one per post, 1–50 characters, no `.` or
  `&`). `--reply-to <post_id>` makes it a reply. Both are part of the digest,
  so changing either after a dry run invalidates that digest.

## 3. Choosing topics

Threads delivers by topic, not by follower count — a zero-follower account
can still be seen if the topic is right, and an accurate-but-wrong topic
reaches no one. Verified patterns:

| Works | Doesn't |
|---|---|
| A verb-shaped thing people are actually doing right now | A category/field name (surprisingly, "large" everyday words like "parenting" or "education" returned zero results) |
| The name of a physical object | An abstract or emotionally loaded word (the conversation drifts to something adjacent) |
| The insider's short form / slang for a topic | A polysemous or industry-specific term (it lands in the wrong industry) |
| — | A word you invented (topics are places people already gather, not places you create) |

Practical notes:
1. Match "is anyone here," not "does this match my article." Content-derived
   keywords are usually empty rooms.
2. Try the insider's abbreviation, not just the full term.
3. Narrower compound phrases usually mean *nobody*, not *more precision*.
4. Check polysemous and technical-sounding words before using them — they
   often belong to an unrelated field or language.
5. Concentrate on a topic that works rather than spreading across many.
6. One topic per post (platform limit); explore one post at a time and check
   back the next day.

Check a topic before using it by logging into Threads and opening
`https://www.threads.com/search?q=<topic>&filter=topic`. Look at three things:
recent activity, whether the people there overlap with your audience, and
whether the language matches. THTH itself cannot query topic search (that
permission hasn't been granted).

## 4. The topic shelf (shared observations, per-account judgments)

`thth topics` stores two different things in one file:

- **Observations** (who's there, what they're talking about) — shared across
  every account, because "who's in the room" is the same fact for everyone.
- **Judgments** (`alive` / `mismatch` / `dead` / `unknown` — does this topic
  fit *my* project) — recorded per account and never mixed into another
  account's fit rate.

Record what you found:

```bash
thth topics <your-account> --note <word> \
  --verdict alive|mismatch|dead|unknown \
  --status ok|empty|permission_denied|unavailable|rate_limited|partial \
  --kind action|dated|common-noun|category|abstract|jargon|proper-noun|connect-with-me|invented \
  --audience "who was there" --by "<you>"
```

Always set `--status`: it distinguishes "checked, zero results" (`empty`)
from "couldn't check" (`permission_denied`, etc.) from "not checked yet"
(omitted). Read back what everyone has recorded with `thth topics history
<word>`. Made a mistake? Retract it (the row stays, marked retracted, for
audit) rather than editing it away:

```bash
thth topics retract-note <note_id> --reason "..." --by "<you>"
```

### What to trust, and what not to

**Trust:**
- `thth topics <account> --advise` counts *your* account's judgments only;
  other accounts' judgments show up as unweighted reference, never mixed
  into your denominator.
- View-count comparisons only use rows where the source and elapsed time
  actually line up; anything that doesn't line up is kept but reported
  separately as "not usable for comparison," never silently dropped.
- Observations are listed one row per observer, newest first — a later
  observer no longer erases an earlier one.
- Retracted rows disappear from every reading surface (`--advise`,
  `suggest`, per-kind rollups) but never from `history`.

**Don't trust:**
- The median shown on a "reference (not usable for comparison)" row — it
  mixes posts with wildly different elapsed times.
- View counts from `thth topics <account>` without `--advise` — those are
  read live from the API at call time, a different number from the ledger's
  24-hour snapshot. Don't compare the two.
- Another account's free-text `audience` note as if it were a promise about
  your results — a note can accidentally describe *that account's own past
  performance* rather than "who is in this topic," because the tool has no
  separate field for the two. Read who wrote it before trusting the number
  in it.
- Assuming the 1–2 observations shown on screen are all of them — check
  `history` or `--json` (`observations_more`) for the true count.

## 5. Scheduling a post while you're away

Write one post per file at `docs/sns/queue/<date>-<slug>.md`:

```markdown
---
thth: 1
account: your-account
publish_at: 2026-09-10T08:00:00+09:00
status: draft
approved_sha:
approved_at:
topic:
reply_to:
post_id:
posted_at:
---
# notes THTH does not read

## threads
The text that actually gets posted. Up to 500 characters.
```

Commit and push it — THTH only posts content that matches what's actually
committed; anything else shows up as `unverified_content` and is not posted
(and not deleted).

### Approval, in two steps

```bash
# step 1: show the full text, write nothing, exit 1
thth approve <file>

# step 2, after a human says yes, with the digest step 1 printed
thth approve <file> --confirm ab12cd34ef56 --by "your-name"
```

Changing the body, account, topic, reply target, or `publish_at` after
approval invalidates it (`approval_stale`); re-run both steps. `--by` is
required — it lands in the front matter and the commit. You can pass a
directory instead of a file to approve every `draft` file in it at once; if
any one file fails lint or is already posted, none of them are approved.

Undo an approval before it posts with `thth revoke <file> --reason "..."
--by "..."` — it returns to `draft` without touching the text. Once a
`post_id` exists, it can't be revoked; delete it from the platform's own UI
if needed.

See what's coming with `thth schedule <account> --days 14`.

## 6. Settings you control

Per-account settings in the account file are yours to tune, not enforced by
THTH's core:

| Setting | Meaning |
|---|---|
| `min_interval_hours` | Minimum gap since the last post. If set above 0, it overrides your `publish_at` times. |
| `quiet_hours` | A time window where nothing posts, even if scheduled inside it. |
| `max_per_run` | Posts per timer tick (default 1). |
| `stale_days` | How long overdue a post can be before it's skipped instead of dumped all at once. |
| `tick_minutes` | Timer interval; how late a post can be before it fires. |
| `hashtags` | Whether `#word` text is allowed in the body. |

## 7. Troubleshooting

- **"inflight" won't clear** — a previous run couldn't confirm whether it
  posted or not. Check the platform manually; don't delete the marker
  yourself. This exists specifically to prevent double-posting.
- **"already running"** — another run overlapped; wait and retry.
- **`approval_stale`** — something changed after approval; re-approve.
- **Token state** — see it with `thth board`; media without a token
  expiration (Bluesky, Mastodon) correctly show "ok / no expiry."
- **`unverified_content`** — the file doesn't match a synced commit; commit
  and push it.

## 8. What to decide for your own project

1. Use the tool and report friction — the file format is still young.
2. Decide, ahead of time, how you'll handle reply topics you don't want to
   engage with (efficacy claims, outcome predictions, comparisons) —
   THTH will post reply drafts, but a human reads every one before it goes.
3. Create `docs/sns/queue/` in your own repo before the timer needs it.

## 9. Account ledgers (`thth account add` / `migrate`)

Each account is one JSON file. It lives **outside this repo**, so nothing you
configure is ever committed here:

| Order | Location | When |
|---|---|---|
| 1 | `$THTH_ACCOUNTS_DIR` | if you set it |
| 2 | `$THTH_ROOT/accounts/` | the normal place — used as soon as the directory exists, even when empty |
| 3 | `<repo>/accounts/` | compatibility only, kept for one release, with a warning on stderr |

`thth doctor` and `thth board` print the directory they actually read, on one
line, even when no ledger was found (`accounts_dir` in `--json`).

Write a new one from the bundled template:

```bash
# Threads: --redirect-uri is the callback you registered with your Meta app
thth account add your-project-threads --media threads --project your-project \
  --redirect-uri https://your.domain/callback/
# Bluesky: --handle is required (a domain-shaped handle)
thth account add your-project-bluesky --media bluesky --project your-project \
  --handle you.bsky.social
# Mastodon: --handle and --instance are required
thth account add your-project-mastodon --media mastodon --project your-project \
  --handle you --instance https://your.instance
```

What each medium needs — **fields the tool cannot guess are asked for, not
filled in silently**:

| Medium | `--handle` | `--instance` | `--redirect-uri` |
|---|---|---|---|
| threads | optional (defaults to `--project`; on Threads the handle is the username, so that usually lands) | — | optional, but **omitting it leaves the template's placeholder** `https://example.invalid/`, and `thth auth` then refuses (exit 2) |
| bluesky | **required** (`name.bsky.social`; the `--project` value never matches) | optional (`service`, defaults to `https://bsky.social`) | — |
| mastodon | **required** (username without the `@`) | **required** (every instance has its own endpoint) | — |

- Written to `$THTH_ROOT/accounts/<name>.json` — **never into the repo**, even
  when the compatibility path above is the one being read.
- Always `production: false` and `scheduled: false`. The tool will not create
  something that posts for real; you turn those on by hand.
- It refuses to overwrite an existing ledger (exit 1).
- `--handle` defaults to `--project` on Threads, not to the account name. On
  Bluesky and Mastodon it is required, with an example in the refusal.
- If you leave a template placeholder in the ledger, **all three commands say
  so by name**: `account add` prints one line as it writes the file, `thth
  doctor` lists them (`dummy_fields` in `--json`, exit 1 — the ledger is
  readable, so not 2, but "no problems" would be a lie), and `thth auth`
  refuses **before printing an authorization URL** (exit 2). The placeholders
  are `redirect_uri: https://example.invalid/`, Mastodon's
  `instance: https://mastodon.example`, and a handle still set to the
  template's (`demo`, or `demo.bsky.social` on Bluesky).

Coming from an older version whose ledgers sat in `<repo>/accounts/`:

```bash
thth account migrate --dry-run   # plan only; creates nothing
thth account migrate             # copy them to $THTH_ROOT/accounts/
```

It **copies** — the repo's working tree is not touched, so a deployment that
tracks the repo with `git merge --ff-only` keeps working. It is idempotent, and
if a file already exists at the destination with different content it says so
by name and exits 1 rather than overwriting. Deleting the old directory is a
separate, human step.

## 10. Asking before you post (`thth ask before-you-post`)

```bash
thth ask before-you-post your-account --topic <word> \
  [--kind 行動|年度付き|一般名詞|カテゴリ|抽象|専門語|固有名|つながり型|自作] \
  [--hour-band 朝|昼|夕|深夜] [--reply] \
  [--window-days 30] [--min-n 20] [--json]
```

**Read-only, and local only.** It reads the ledgers THTH has already collected
into your own repo — nothing is fetched, nothing is written, no repo or state
file changes. `provenance.source` is `"local"`. This is *not* the shared spring
described in [設計_v2_泉と門_2026-09-13.md](設計_v2_泉と門_2026-09-13.md) §1;
that does not exist yet.

**Your draft is not an input.** You pass a word, a shape, an hour band and
whether it's a reply. The body never reaches it, and never appears in its
output.

What comes back (`--json`, seven keys):

| Key | What it is |
|---|---|
| `summary` | one line you can read aloud |
| `expected` | `branches_24h`, `first_reply_min`, `views_24h` — each with median, p25/p75 and `n` |
| `comparable` | `n`, `window_days`, what was aligned on, and the `medium` |
| `cannot_say` | every question it declined, with the reason |
| `one_thing_to_change` | at most one suggestion, or `null` |
| `audience` | who was observed in that topic — one row per topic, listed **per observer** |
| `provenance` | `source`, number of observers, when, schema version |

Each `audience` row is `{"topic", "observers", "latest", "views", "views_more"}`.
`observers` is how many observers wrote about that word, `latest` the newest
observation date, and `views` the **newest free-text note from each observer**,
newest first, **at most three**; `views_more` is how many observers did not fit
(it is always present, `0` included). **No observer's name or pseudonym is ever
returned** — each view is just `{"who", "latest"}`, where `who` is the
free-text note and `latest` that observer's newest date. One note beside a
count of three observers read as "three people said this"; **a shared shelf
does not hold a single truth.** The full history stays local, in
`thth topics <account> --advise` and `thth topics history`.

**Expect `cannot_say` almost everywhere at first.** A median is only returned
once at least `--min-n` (default 20) comparable posts exist inside
`--window-days` (default 30); below that the number is withheld and the reason
(`n=5`, and so on) is listed instead. One account's first weeks will not reach
20. **That is the answer, not a failure** — exit code stays 0. Comparisons
never cross media: Threads' 24 hours and Bluesky's 24 hours are different
numbers, and the `medium` in `comparable` says which one you got.

An unknown `--kind` or `--hour-band`, or an empty `--topic`, is refused with
exit 2 rather than quietly ignored. An account with no ledger exits 1.

The same thing is available over MCP as the `before_you_post` tool.

## 11. Contributing observations (`thth share`)

```bash
thth share            # or: status — where it is, on or off, how many bytes
thth share on         # start queuing (also: sync, immediately)
thth share off        # stop
thth share log        # print every line ever queued
thth share sync       # re-queue from the ledgers you already have
```

**Off by default**, and off in every ambiguous case: if
`$THTH_ROOT/state/share/config.json` is missing, or unreadable, or malformed,
the answer is off. While off, the outbox is **zero bytes** — the pseudonym file
and the hash salt are not even created.

**There is nowhere for it to send.** Turning it on appends lines to
`$THTH_ROOT/state/share/outbox/<YYYY-MM>.ndjson` on your own disk. No server
exists to receive them; `thth share` opens no socket. `thth share log` prints
every line, so you can read exactly what you would be contributing before
anyone ever asks for it.

What it queues:

- **Observations** — the topic word, the free-text `audience` note, the topic
  kind, the fetch status, a pseudonym for the observer, and the date.
- **Thread shape** — a **salted SHA-256** of the post id (the salt stays on
  your disk and is never queued, so the hash cannot be turned back into a
  permalink), the medium, the kind, the word, the hour band, and the counts at
  each collection tick.
- **Your pseudonym** — a random 16-digit id in
  `$THTH_ROOT/state/share/observer_id`, stable across runs. It is the only name
  that ever appears.

What never gets queued, enforced by a check that raises before the line is
written (`_assert_clean`): post bodies, reply bodies, **repliers'** usernames,
your own `verdict`/`by`/`note`/`reason`, account names, access tokens, repo
paths, and raw post ids.

**The check cannot strip what you typed yourself.** `audience` is a free-text
note you write with `thth topics --note --audience "…"`, and it is queued as
written. What the machine drops is the *replier's* `username` — a field that
comes from the platform's ledger — not characters you chose. Describe who was
in the room by attribute ("parents comparing schools"), never by handle.

Turning it on is recorded locally with `--by`; that name is not queued.
