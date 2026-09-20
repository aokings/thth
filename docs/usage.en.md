# THTH usage guide (English summary)

This is a summary of
[使い方_プロジェクトのセッション向け_2026-09-09.md](使い方_プロジェクトのセッション向け_2026-09-09.md)
(§2–§8) and
[引継ぎ_トピックの棚_アカウント側セッションへ_2026-09-12.md](引継ぎ_トピックの棚_アカウント側セッションへ_2026-09-12.md)
(§2). Where the two disagree, the Japanese original is the source of truth —
it is the operational document, updated as reality changes. Examples below
use `your-account` in place of any real account name.

**Where to start** (the same three lines `thth --help` opens with)
- Post one draft, once → `thth send` (a rehearsal by default; nothing goes out
  until you add `--production`) — §2
- Run an account from a queue → `thth lint` → `thth approve` (two steps) →
  `thth throw` — §5
- Ask before you post → `thth ask before-you-post` — §10

## 1. What it does, what it doesn't

THTH takes a body of text and posts it: it writes the `post_id` back, and
collects replies and metrics into your repo. It does **not** decide what to
write, how often, when, or which topic to use — that is entirely your
project's call. It sends your text as given, without reformatting or
truncating it. Platform caps (e.g. Threads' 250 posts / 1,000 replies per 24h)
are not enforced by THTH itself.

Setting up a new account? Read §9 (where the account ledger file lives) first —
it is not in this repo. §10 is `thth ask before-you-post`, §11 is reading
before you reply (`thth where` / `thth thread` / `thth who`) and `thth after`.

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
- `thth collect <your-account>` gathers metrics and replies for posts sent this
  way as well; if the account has no repo, the ledgers are written to
  `$THTH_ROOT/state/<account>/data/sns/` (nothing is committed or pushed), and
  `thth measured` / `thth replies` / `thth threads` read them from there and
  label them `出所=同席の送信` (`source: "sent"` in `--json`).

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
whether the language matches.

### Finding somewhere to reply (`thth topics … --search`)

```bash
thth topics <your-account> --search <word> [--recent] [--json]
```

1. Each row is **time · @author · `post_id` · replies · mark**, with the
   **permalink and the first 60 characters of the body** underneath. The body
   is printed, never stored — and it is not in `--json` at all.
2. The **replies** column is the count when the medium returns one. Threads'
   keyword search returns only `has_replies`, so you get `有` / `無`
   (yes / no); `—` means *unknown*, **never zero**. The summary line above the
   list ("返信だった投稿") counts something else: posts that are themselves
   replies.
3. The **mark** means your own queue already holds a draft with
   `reply_to: <post_id>`: `[返信済]` posted, `[承認済]` approved but not sent,
   `[下書き]` draft. **No mark means "no draft found in this queue"**, not
   "nobody has replied". If the queue can't be read, no marks are printed and
   the tool says so (`replied_lookup.available: false` in `--json`).
4. To reply, put `reply_to: <post_id>` in a draft's front matter (§5 — it works
   for other people's public posts too), then the usual `thth lint` →
   `thth approve` (two steps) → `thth throw`.
5. Your reply is your own post, so `thth collect` picks up its views, likes and
   replies; `thth replies` shows which branch got a response.
6. **THTH does not decide who to engage with.** It hands you the material (who,
   when, how many replies, whether you already replied) and the gate (human
   approval). **The Threads API has no trending endpoint** — "hot" can only be
   inferred from TOP ordering, recent timestamps and reply volume. `--json`
   gives `posts[]` with `post_id`, `permalink`, `timestamp`, `author`,
   `replies`, `has_replies` and `replied`.

A medium without the `keyword_search` capability (Bluesky, Mastodon today) says
so in one line and exits non-zero — it never prints an empty list.

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
configure is ever committed here. **A fresh clone contains no ledgers** — the
in-repo `accounts/` directory was removed on 2026-09-14, and only the templates
in `accounts.example/` are shipped, so `thth account add` is your first step:

| Order | Location | When |
|---|---|---|
| 1 | `$THTH_ACCOUNTS_DIR` | if you set it |
| 2 | `$THTH_ROOT/accounts/` | the normal place — used as soon as the directory exists, even when empty |
| 3 | `<repo>/accounts/` | compatibility only, kept for one release, with a warning on stderr — a fresh clone has no such directory, so this applies only to a machine that has not migrated yet |

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
separate, human step — this project took it on 2026-09-14, once its own
deployment had migrated and one scheduled run had gone through.

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

## 11. Reading before you reply (`thth where` / `thth thread` / `thth who`) and `thth after`

These four commands only read — none of them writes to `data/`, and none of
them sends anything anywhere (design "自分の泉"):

- **`thth where (<account>|--project P) <word…> [--recent] [--limit N] [--json]`**
  — where to go engage next: overlays your own history on top of a live
  keyword search (1–5 words), one section per account (no cross-account
  totals, no ranking). `--project` runs it across every account in that
  project; an account that can't be read there is dropped into `cannot_say`
  and the rest continue. A single, unreadable `<account>` is a loud failure
  (exit 1, or `{"error", "account"}` with `--json`) rather than a silent empty
  answer.
- **`thth thread <account> <post_id> [--since ISO] [--max-messages N] [--json]`**
  — read a thread's branches on the spot. Nothing is saved.
- **`thth who (<account>|--project P) (<author_key>|@<username>) [--profile] [--json]`**
  — a pseudonym's history with you: how many times you've crossed paths, when,
  and how they reacted — never what anyone wrote. `--profile` fetches a live
  public profile (Threads only) without storing it. A single, unreadable
  `<account>` exits 1, the same as `where`.
- **`thth after (<account>|--project P) [--reply-to ID] [--author-key KEY] [--topic WORD]
  [--hour-band BAND] [--window-days N] [--min-n N] [--json]`** — after you've
  posted or replied, how it landed: owned measured root posts and replies are
  separate sections, with 24-hour views, counts, and a window, never bodies.
  A project result contains per-account sections only; it has no cross-account
  totals or ranking. `--kind` uses the current topic-shelf classification, not
  the post structure (`form`) or a historical classification.

All four answer from **your own local ledgers only**; none of them talks to a
spring or a shared pool. Each records one minimal line per call in
`runs-YYYY-MM.ndjson` (`account`, `run_id`, `mode: "read"`, `action`, `status`,
`error`, plus a few call-specific counts) — never post bodies or usernames.

The same four are available over MCP as `where_to_appear`, `thread_read`,
`who_is_this`, and `after_you_posted`.

## Authorization in 2.11

Masaru operates the clients, server and ledgers. Invited users approve the URL; they do not set up a VM or developer app. The operator uses `thth auth <account> --by masaru` for Threads, supported Mastodon instances and X. X remains auth-only. Bluesky uses an App Password through `token set --stdin --by`; this is not atproto OAuth. Do not put credentials or authorization URLs into an LLM conversation. See the [unified guide](導入_承認を押すだけ.md) for operator JSON client input, `op read` examples, scope/probe distinctions and refresh limits. A connection page is future work.
