# THTH

THTH is a command-line tool for **researching, posting to, scheduling on and reviewing** your own social media accounts (Threads, Bluesky, Mastodon, X).

Because it is a CLI, an AI assistant such as Claude Code can drive it directly. The bundled MCP server and skills only make that easier. THTH does not decide what to write, and it acts only when asked.

Japanese: [README.md](README.md) · Site: https://thth.me · Docs index: [docs/README.md](docs/README.md)

**Where to start**
- Post one text, once → `thth send` (a rehearsal by default on a local ledger; nothing goes out until you add `--production`)
- Research a topic → `thth topics <account> --search <word>`, `thth where <account> <words>`
- Run an account from a queue → `thth lint` → `thth approve` (two steps) → `thth throw`
- Ask before you post → `thth ask before-you-post`

## What it does

| | Threads | Bluesky | Mastodon | X |
|---|---|---|---|---|
| Post and schedule | yes | yes | yes | yes |
| Delete | yes | no | yes | yes |
| Collect replies and insights (views, likes, replies and more) | yes | yes | yes | no |
| Keyword search (`thth topics --search`, `thth where`) | yes | yes | yes | no |
| Mentions (`thth mentions`) | yes | yes | yes | no |
| Profile and place lookup | yes | no | no | no |

- **Research**: `thth topics <account> --search <word>` shows counts, distinct authors and the latest posts for a word. `thth where` finds where to join a conversation next, with your own history layered on top.
- **Post**: `thth send` publishes now, `thth schedule` schedules, and `--reply-to` replies.
- **Review**: `thth collect` fetches replies and insights at fixed ages after posting (1 hour to 30 days); read them with `thth replies` and `thth measured`.
- **Reposts and likes**: `thth repost`, `unrepost`, `like`, `unlike` (since 3.15.0). Threads supports reposts only (its API has no likes). Bluesky supports both. Likes on Mastodon and X need the account to be authorized again ([release notes 3.15.0](docs/リリースノート_3.15.0_2026-10-09.md)).

## Getting started

### If you were invited

Open the invitation link from the operator (gotoq) and authorize with Threads. Then, in a terminal:

```bash
pip install thth
thth login
```

A browser opens: enter your account name and account secret and allow the device. From then on the command works on your own account.

```bash
thth posts <account>
thth topics <account> --search coffee
thth send <account> --text "Hello"
```

You do not need a server, a Meta app or git. The full guide (Japanese) is [招待されたら](docs/導入_招待されたら.md).

### If you run accounts on your own machine (operator)

Keep account ledgers on your machine, approve draft files, and let a timer post them. Approval is two steps (show the text and a digest, then confirm with that digest), and the record lands in git. See the [unified guide](docs/導入_承認を押すだけ.md) and the [full walkthrough](docs/usage.en.md).

## Using it from an AI assistant

```bash
claude mcp add thth -- thth-mcp
```

The MCP tools mirror the CLI and call the same `thth` command. The skill (`skills/thth/`) tells the assistant what to check before posting and how to handle replies. The key is never given to the assistant: the `thth` command uses the key that `thth login` saved locally.

## Guard

Every account has a guard that stops a misbehaving assistant.

| Limit | Default |
|---|---|
| Minimum interval between posts (not for replies) | 6 hours |
| Posts per day | 8 |
| Deletions per day | 5 |
| Burst | more than 3 in 10 minutes stops the account |
| Quiet hours | no immediate posts 22:00–07:00 |

An assistant can only tighten these limits. Loosening them, resuming a stopped account and issuing or revoking the key are done by the owner on https://thth.me/activity with the account secret.

## What it does not keep

- Search results (`topics --search`, `where`, `thread`, `who`): text and authors are shown, not saved.
- It never reads direct messages.
- Tokens and secrets are redacted from logs and output.

See the [privacy policy](https://thth.me/privacy/).

## Install

```bash
pip install thth
thth --version
```

Zero runtime dependencies; `thth` and `thth-mcp` entry points. Released on [PyPI](https://pypi.org/project/thth/) and listed in the [MCP registry](https://registry.modelcontextprotocol.io/?q=io.github.aokings/thth) as `io.github.aokings/thth`. From source: `git clone`, then `python -m thth --version`.

## Commands

All subcommands `thth --help` lists, one line each:

- `send` — post one text now (`--reply-to` for a reply); on an invited account it goes through thth.me
- `schedule` — schedule a post, or list what is due in date order
- `posts` — list your own recent posts
- `retract` — delete a published post (two-step confirmation on a local ledger; the record is kept)
- `repost` — repost a post (Threads, Bluesky, Mastodon, X; only when the owner asks)
- `unrepost` — undo a repost made through THTH
- `like` — like a post (Bluesky, Mastodon, X; the Threads API has no likes)
- `unlike` — undo a like made through THTH
- `replies` — read collected replies (`--refresh` fetches first)
- `measured` — read collected insights
- `collect` — fetch replies and insights on the elapsed-time schedule
- `unanswered` — replies to your posts that you have not answered yet
- `mentions` — mentions of your account
- `topics` — keyword search (`--search`), and how much each topic was seen
- `where` — where to engage next: search results with your own history layered on top
- `thread` — read a post's branch on the spot; nothing is saved
- `who` — pseudonymous history of how often you crossed paths with someone; never what was said
- `profile` — look up a public profile (Threads)
- `location` — search for a place (`thth location search <account> <word>`, Threads)
- `login` — authorize this device in the browser and save the thth.me key (`--stdin` to paste a key instead)
- `logout` — remove the saved thth.me key
- `account` — an account's status and guard (`account status`, `account set`); on a local ledger, `account add` and `account migrate`
- `doctor` — read-only check of what a token can do
- `observe` — one page for the start of a session: unanswered replies and mentions, yesterday's posts, the topics you watch, and next steps
- `morning` — alias of `observe`
- `after` — how the conversations you joined were received
- `threads` — thread shape: branches, depth, participants, time to first reply
- `ask` — `before-you-post`: what happened last time with this word, hour and shape (local ledgers only)
- `analytics-report` — read-only activity snapshot with periods, sample sizes and missing data
- `study` — add a post to a local study declaration
- `study-report` — link a declared study to your own post observations; no causal claims
- `handoff-report` — local operations evidence for a session handoff
- `plaza` — share what you tried and how it went across your accounts
- `map` — the topic map: the topics you chose, with your own numbers on them
- `report` — file a bug or request inside the tool and read the replies
- `forms` — vocabulary for post shapes
- `lint` — check a queue file's front matter and length
- `preview` — show the exact text that would go out
- `approve` — two-step approval of a queue file (show, then confirm with a digest)
- `revoke` — undo an approval and return the file to draft
- `queue` — draft, approved and posted counts and what is next
- `throw` — post one approved file (dry run by default)
- `run` — throw, collect and refresh in one call (what the timer runs)
- `systemd` — generate a timer and service unit
- `board` — freshness, in-flight state and malformed files per account
- `pull` — fetch and fast-forward an account's repo
- `inflight` — inspect and resolve a publish whose outcome is unknown (two-step confirmation)
- `auth` — operator: start authorization for an account
- `refresh` — refresh a long-lived token
- `maintain` — keep every account's token alive
- `token` — operator: set a token or app password through stdin
- `app` — operator: store or show the app client settings
- `notifications` — configure and test incident emails
- `worker` — operator daemon: finishes invitations and carries out what owners request on thth.me
- `serve-reports` — private read-only report HTTP over a Unix socket (development grade)
- `admin` — operator: invitations and read-only inventory, log, token and release reports

## License

MIT.
