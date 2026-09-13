<!-- source: README.en.md (this repo) fetched: 2026-09-13 -->
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
- Never sends anything to a third party. `thth share` is **off by default** and,
  even when you turn it on, **there is nowhere for it to send**: it only appends
  to a local file under `$THTH_ROOT/state/share/outbox/`, and `thth share log`
  prints every line of it. What it queues is words, audience notes, topic kinds,
  counts, time-of-day bands and a **salted hash** of the post id — never post
  bodies, replies, **repliers'** usernames, your own verdicts, account names,
  tokens, repo paths, or raw post ids — see
  [docs/設計_v2_泉と門_2026-09-13.md](docs/設計_v2_泉と門_2026-09-13.md) §2.
  The one thing the machine cannot strip is **what you typed yourself**: the
  free-text `audience` note is queued as written, so describe who was there by
  attribute ("parents comparing schools"), not by handle.
- Never reads direct messages. It only ever touches public posts and public replies.
