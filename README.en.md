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
| Delete | yes | yes | yes | yes |
| Collect replies and insights (views, likes, replies and more) | yes | yes | yes | insights only (3.16.0, posts sent through THTH) |
| Keyword search (`thth topics --search`, `thth where`) | yes | yes | yes | no |
| Mentions (`thth mentions`) | yes | yes | yes | no |
| Profile and place lookup | yes | no | no | no |

- **Research**: `thth topics <account> --search <word>` shows counts, distinct authors and the latest posts for a word. `thth where` finds where to join a conversation next, with your own history layered on top.
- **Post**: `thth send` publishes now, `thth schedule` schedules, and `--reply-to` replies.
- **Review**: `thth collect` fetches replies and insights at fixed ages after posting (1 hour to 30 days); read them with `thth replies` and `thth measured`. For X posts sent through THTH, posts created within the last 30 days also get link clicks (`link_clicks`), profile clicks (`profile_clicks`), `engagements`, non-public impressions (`private_impressions`) and, for a post with one video, its views and playback counts (`video_views`, `video_playback_0` to `100`); the 30-day (720-hour) mark gets public counts only, and a count that is not returned is left out, never written as 0.
- **Reposts and likes**: `thth repost`, `unrepost`, `like`, `unlike` (since 3.15.0). Threads supports reposts only (its API has no likes). Bluesky supports both. Likes on Mastodon and X need the account to be authorized again ([release notes 3.15.0](docs/リリースノート_3.15.0_2026-10-09.md)).

## Getting started

### If you were invited

Open the invitation link from the operator (gotoq) and authorize with the invited service (Threads, Mastodon, a Facebook Page or Instagram). Then, in a terminal:

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

Keep account ledgers on your machine, approve draft files, and let a timer post them. Approval is two steps (show the text and a digest, then confirm with that digest), and the record lands in the approval ledger on the VM (not in your repo, since 4.4.1). See the [unified guide](docs/導入_承認を押すだけ.md) and the [full walkthrough](docs/usage.en.md).

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

- Search results (`topics --search`, `where`, `thread`, `who`): text and authors are shown, not saved. `where … --keep` saves only per-search-term numbers (numbers below 5 are missing; read with `where-log`). `topics --search <word> --tag --keep` (Threads, 5.7.0) saves only the number of posts that carry the word as their topic, the number of distinct authors, and the dates of the newest and oldest (numbers below 5 are missing; no exact times) in the same project-private place and under the same rules as `where … --keep` (read with `where-log`).
- Direct messages are read only when you run `thth dm` (Facebook Page and Instagram inboxes). Text and the other person are shown, never stored; a reply is sent by a person in two steps (no automatic replies, only within 24 hours of their last message). The stream address `thth live create` returns is shown, not stored.
- Tokens and secrets are redacted from logs and output.

See the [privacy policy](https://thth.me/privacy/).

## Install

```bash
pip install thth
thth --version
```

Zero runtime dependencies; `thth` and `thth-mcp` entry points. Released on [PyPI](https://pypi.org/project/thth/) and listed in the [MCP registry](https://registry.modelcontextprotocol.io/?q=io.github.aokings/thth) as `io.github.aokings/thth`. From source: `git clone`, then `python -m thth --version`.

Test notes for agents: [THTH on headlesson](https://headlesson.com/apps/thth) (same operator as THTH) — install and `--help` of 3.15.0 tested on 2026-10-11; posting and scheduling not tested.

## Commands

All subcommands `thth --help` lists, one line each:

- `send` — post one text now (`--reply-to` for a reply); on an invited account it goes through thth.me
- `schedule` — schedule a post, or list what is due in date order
- `posts` — list your own recent posts
- `retract` — delete a published post (two-step confirmation on a local ledger; the record is kept)
- `edit` — correct the text of a post THTH published (two-step confirmation; Facebook only; the previous text stays in the record)
- `reply` — hide, unhide or delete a comment (`reply hide|unhide|delete`; delete is two-step; Facebook and Instagram; Threads replies can be hidden and unhidden but not deleted), and on Threads list, approve or ignore the replies waiting on a post that requires reply approval (`reply pending <account> <post_id>`, `reply approve|ignore <account> <reply_id>`; local ledger only; reply text is shown, not stored)
- `comments` — open or close the comments on a post (`comments open|close`; Instagram)
- `page` — a Facebook Page's call-to-action buttons (`page cta show|delete`; delete is two-step)
- `live` — create a Facebook Page live video and get its stream address, or end it (`live create|end`; two-step)
- `dm` — read the Page and Instagram inboxes and reply within the 24-hour window (`dm list|read|send`; send is two-step; nothing stored)
- `brand` — branded content on Instagram and Facebook (`brand creators list|approve|remove` the approved-creators list, `brand promote` allows or revokes a brand's use of a tagged post, `brand ads-permissions`, `brand content --business`; approve, remove and allow/deny are two-step; nothing stored)
- `creators` — search the creator marketplace (`creators search`; read-only, ids only, nothing stored)
- `wa` — WhatsApp (`wa add|auth`; `wa numbers|templates` read; `wa template create` and `wa send` are two-step, templates only, recipient number and text never stored)
- `shop` — Instagram product tags (`shop eligibility|catalogs|products|tags` read; `shop tag` adds a product tag to an existing post and is two-step; nothing stored)
- `register` — the owner's own register (a folder of text files on their computer): check rows, confirm them, list what is missing and pick a few questions, make unapproved post drafts from confirmed rows of a physical shop, and make text the owner pastes into Google Maps, Tabelog and similar places (`register add|check|show|use|confirm|deny|remove|triggers|draft|holes|ask|answer|questions|paste|pasted|compare`; no network access; THTH never reads or signs in to those places)
- `project` — one media (project): `project leave <media> --by <name>` removes every account of that media and THTH's copies of its register state; the first run only shows what would be removed and a digest, `--confirm D` removes, and a stopped run continues when repeated; the register folder itself is kept
- `owner` — the whole owner: `owner leave --by <name>` removes every account, THTH's copies of all register state, the principals and their keys kept by THTH, and the seal private key; two-step like `project leave`; every register folder is kept
- `quota` — read the platform usage headers, any rate-limit wait and (Instagram) the daily publishing limit for an account (read-only; Facebook and Instagram)
- `repost` — repost a post (Threads, Bluesky, Mastodon, X; only when the owner asks)
- `unrepost` — undo a repost made through THTH
- `like` — like a post (Bluesky, Mastodon, X, Instagram; the Threads API has no likes)
- `unlike` — undo a like made through THTH
- `list` — X private lists: create, add or remove a few hand-picked people (10 at most per call), read a list's posts (X only; never public lists)
- `community` — search X Communities (`community search`; paid read, not stored). Post into one with `send --community <id>` or front-matter `community_id:` (X only; join in the X app first; not combined with replies)
- `trends` — X trends for a place (WOEID, default Japan; one paid read, not stored; X only)
- `ads` — a Meta ad account: `add` a ledger (ceilings 0, flags off), `auth` a long-lived user token (`--exchange` tries a re-exchange), `accounts`, `doctor`, `show`, `insights`, `collect`; `boost` turns a Page post into an ad with everything PAUSED, and only the two-step `activate` starts it. `boost --platforms` takes `facebook`, `instagram` and `threads`; Meta delivers to Threads only together with the Instagram feed, so `threads` without `instagram` is refused before any request (`ads_threads_needs_instagram`), the creative carries the Threads account (`threads_user_id` from the ledger, or read from the Instagram account's `connected_threads_user`), and a Threads post itself cannot be boosted (`ads_threads_post_unsupported`; boost the same content posted to the Page instead). The ad token now also asks for `threads_business_basic` (added 11/07); a token issued before that is refused for Threads before any request until it is re-authorized with `thth ads auth <name> --by <name>` (`ads_threads_scope_missing`). Threads delivery is not yet confirmed against the real API. Spend caps and THTH's ceilings are never raised from THTH
- `gads` — Google Ads, read-only: `auth` runs a one-time OAuth for the user's own Google account (installed-app client, loopback redirect on 127.0.0.1, PKCE; the refresh token is kept in a 0600 file and never printed), `accounts` lists the customers the grant can reach, `keywords --customer <id> --seed <word>…` returns keyword ideas with average monthly searches, competition and the top-of-page bid range (KeywordPlanIdeaService), and `report --customer <id>` shows impressions, clicks, cost and conversions per campaign for a date range. There is no write command yet.
- `gsc` — Google Search Console, read-only: `auth` runs the same one-time OAuth flow with the same installed-app client as `gads`, asking only for `webmasters.readonly` and keeping its own refresh token in a separate 0600 file (`~/.config/thth/google-gsc-token.json`, override with `$THTH_GOOGLE_GSC_TOKEN`); `sites` lists the properties and permission levels the grant can see, `query --site <property>` returns clicks, impressions, CTR and average position by `--dim query|page|country|device|date` (default: the 28 days ending 3 days ago, Pacific time, final data only; filter with `--page-prefix` and `--query-contains`), `compare --site <property>` sets the last N days against the N days before and lists the rising and falling queries or pages, and `inspect --site <property> --url <page>` shows the URL Inspection result for the indexed version. There is no write command.
- `webhook` — Meta Webhooks (stage 6 of the Meta plan): `subscribe` a Facebook Page (`feed`, `mention`; an Instagram account subscribes its parent Page to try `mentions`) — adds fields, never removes them; `pull` once from the Worker (normally `thth worker` pulls every 30 s); `status` (read-only). Deliveries feed `replies`, `mentions` and `events` (CLI only)
- `principal` — the authenticating principal (one Hatena ID) behind Hatena Blog accounts: `add`, `show`, `set` limits, `resume` after a stop (CLI only); `auth` arrives in 4.0 S2
- `fotolife` — a principal's Hatena Fotolife image ledger (images THTH uploaded just before publishing an article; one per content sha256); read-only, CLI only (`thth.fotolife/1`)
- `counts` — how many X posts matched a query over the last 7 days, by hour or day (one paid read, not stored; X only)
- `archive` — other people's public X posts from the full archive (since 2006), e.g. `--from <handle> --title <song>`; per-call, total and page caps are always bounded, `--count-only` counts instead (paid reads in the monthly X budget, daily cap `x_archive_daily_reads` defaults to 0, never from timers, not stored; X only)
- `replies` — read collected replies (`--refresh` fetches first)
- `measured` — read collected insights
- `collect` — fetch replies and insights on the elapsed-time schedule
- `unanswered` — replies to your posts that you have not answered yet
- `mentions` — mentions of your account
- `topics` — keyword search (`--search`), and how much each topic was seen
- `where` — where to engage next: search results with your own history layered on top
- `where-log` — per-search-term numbers kept with `where … --keep` (hit rates, aggregates; numbers below 5 are missing)
- `embed` — embed HTML for a public Threads, Facebook or Instagram post (oEmbed; shown, not stored)
- `upcoming` — upcoming events hosted by an Instagram account (shown, not stored)
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
- `events` — what happened, in time order (posts, retractions, replies and mentions received, stopped accounts, pending publishes, budget refusals); read-only, versioned JSON (`thth.events/1`)
- `scenario` — campaigns and series declared next to the drafts (`docs/sns/scenarios/<id>.md`): where each one stands, what is missing next (nouns, no draft text), what has stalled, and the approved stock per series; read-only, versioned JSON (`thth.scenario/1`). Since 5.7.0 a stage can count ads (`- boost: ads`, read from the ad account's own records) and own-article bookmarks apart from other pages (`hatena_bookmark target=own|external`); `scenario site-import <project> --file <json>` takes the GA4 visit counts the hub reads (`hub.site_visits/1`), ties each campaign to a draft and its `thread:`, and shows the visits in `scenario show` and `dashboard … campaign`; `scenario skill --print` prints the flow skill `thth-flow`. Since 5.9.0 `scenario seeds <project> [--days 28] [--limit 5]` lists seeds for the next campaign in four columns (own posts with the most replies back, campaign drafts with the most article visits, own posts with the most views, and media declared but unused in recently closed campaigns), ordered by count only, per medium and per site, without comparisons below n=5 and without anyone else's reply text or username; read-only, it chooses nothing. Since 5.10.0 a stage with `replies_within_hours=` counts replies whatever its name (e.g. `engage`), not posts; a campaign whose stages are all met is `done` (not stalled) and is suggested for closing or for the plaza (`scenario_done` in `events`, a line in the dashboard's attention list until it is posted to the plaza); the dashboard also lists approved drafts more than an hour past their time (held and expired drafts excluded); seeds say `metric_unavailable:<medium>` for numbers a medium does not provide (views on Bluesky and Mastodon) ([release notes 5.10.0](docs/リリースノート_5.10.0_2026-11-03.md)). Since 5.11.0 `scenario show`, `dashboard … campaign`, seeds (a `clicks` column) and the plaza's `--from scenario` numbers show Threads link clicks per post over 72 hours (`threads_clicks`, THTH's own measurement, the same computation as `map`), in a field separate from GA4 visits and never added to them, with the denominator (posts that can be counted / Threads posts) and reasons; a post whose link another own Threads post used within 72 hours — even in a different campaign — is `url_shared_72h`; other media are null with `per_post_clicks_unavailable:<medium>`. The replies column adds `nonzero` and marks counts taken from the reply ledger with `replies_source: ledger` ([release notes 5.11.0](docs/リリースノート_5.11.0_2026-11-03.md))
- `summary` — the numbers per account (posts and reactions in 24h/7d, unanswered replies, followers and day-over-day change, whether it is stopped); read-only (`thth.summary/1`)
- `dashboard` — the contents of the in-conversation dashboard (all media, one project, one campaign or series) for the LLM to paste into the templates in `skills/thth/dashboard/`; read-only (`thth.dashboard/1`). Since 5.11.0 the project dashboard has `site_referrers`: per site, referrer hosts and session counts only (no path, username or text; IP addresses and malformed values are dropped and only counted; `referrers_unavailable` when the hub sent none), not tied to campaigns
- `deliveries` — the delivery ledger (Hatena Blog articles: state, sequence number, history) for an account or project; read-only (`thth.deliveries/1`)
- `media` — the media traits ledger bundled with THTH (how each platform is assumed to judge spam, good behaviour, penalties, API limits; each item with its kind of evidence, confidence, URL and date): `traits [<media>]`, `check`; read-only (`thth.media-traits/1`)
- `article` — `diff`: which published Hatena Blog articles you changed in the repo (HEAD) since they went out, with the body diff and, with `--draft`, the text of an update manuscript to save; read-only, writes no file (`thth.article_diff/1`); `reactions --article <id>`: the reactions to one article lined up by source, attribution and window (Hatena Bookmark counts and third-party bookmarks, Hatena Star, the SNS announcements' metrics and clicks), never summed; your own principals' bookmarks and stars are kept out of `others`; comment text only with `--comments`; `--fetch` reads Hatena once now without writing the ledger (`thth.article_reactions/1`)
- `conflict` — Hatena Blog deliveries edited outside THTH: list them, `show` the current text against what was sent, `check` published ones against Hatena, and `resolve` (`--adopt`, `--gone`, `--no-follow`) with a two-stage confirmation
- `hatena` — `collect`: the same reaction collection a Hatena Blog account's `thth run` does at its end (bookmark counts, bookmarks and stars at 1/6/24/72/168/720 hours after publishing, appended to `data/hatena/reactions/` in the lane repo); reads only the marks already crossed
- `bookmark` — `add`/`delete`: a two-stage approval to bookmark your own published Hatena Blog article (`--delivery`) or a page your lane references (`--url`), at most once per page across the principals of one owner; `reconcile` checks bookmarks whose result was unknown; `list` shows them
- `after` — how the conversations you joined were received
- `threads` — thread shape: branches, depth, participants, time to first reply
- `ask` — `before-you-post`: what happened last time with this word, hour and shape (local ledgers only)
- `analytics-report` — read-only activity snapshot with periods, sample sizes and missing data
- `study` — add a post to a local study declaration
- `study-report` — link a declared study to your own post observations; no causal claims
- `handoff-report` — local operations evidence for a session handoff
- `plaza` — share what you tried and how it went across your accounts. Since 5.8.0 `plaza post <account> --kind measure --from scenario <project> <thread id>` attaches a closed campaign's results as tool-observed numbers (posts per stage, per-medium reactions computed like `after`, article visits or null with a reason, the ads stage without spend); `observe` suggests it when a campaign closes, and `plaza digest` lists each campaign result with the media not yet tried. Since 5.11.0 `plaza post … --preview` builds the whole post (body, observed numbers, draft, default scope) and shows it without writing anything, for any scope — run it first, then drop `--preview` to place it. `plaza hide <id> --as <account> --reason <why> --by <name>` lets the project that placed a post hide its own post; only the administrator brings a hidden post back (`admin plaza unhide <id> --reason <why> --by <name>`). The `--from scenario` numbers also carry Threads link clicks (`threads_clicks`, below)
- `map` — the topic map: the topics you chose, with your own numbers on them
- `report` — file a bug or request inside the tool and read the replies
- `forms` — vocabulary for post shapes
- `lint` — check a queue file's front matter and length
- `preview` — show the exact text that would go out
- `approve` — two-step approval of a queue file (show, then confirm with a digest)
- `approvals` — list the approval ledger on the VM (approvals are not committed to your repo since 4.4.1)
- `revoke` — undo an approval and return the file to draft
- `withdraw` — close a draft you decided not to publish (recorded in the VM approval ledger; `--undo` reopens it)
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
