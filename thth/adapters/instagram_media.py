"""Instagram の画像の入れ物の 2 段（Meta 計画 段 3・B-2・10/29）。

流れ（**L2**: content-publishing）: 画像ごとに relay へ上げて公開の URL を貸す（`media_relay`・Worker の
`/m/`・Threads と同じ）→ `POST /{ig}/media`（`image_url`・`alt_text`。2 枚以上は `is_carousel_item`）→
`GET /{container}?fields=id,status_code` で `FINISHED` を待つ（1 分に 1 回・5 分まで）→ 2 枚以上なら
`POST /{ig}/media`（`media_type=CAROUSEL`・`children`・`caption`）を同じく待つ → `POST /{ig}/media_publish`
（`creation_id`）。**再試行しない。**

失敗の振り分け（`threads_media.publish` と同じ語）:

| 段 | 何が起きた | failure |
|---|---|---|
| 入れ物を作る前（上限・検査・relay） | 出ていない | `publish_definite`（直前の関門で止めたら `publish_vetoed`） |
| 入れ物を作ったあと・公開の前（4xx・ERROR・EXPIRED・止めた） | 出ていない。入れ物は 24 時間で消える | `media_held`（inflight に残す） |
| 状態が 5 分で `FINISHED` にならない | 出ていない見込みだが入れ物が残る | `media_ambiguous`（inflight に残す・計画の「5 分で諦めて publish_ambiguous」の添付の道の語） |
| 作成・公開の要求の時間切れ・5xx | 出たか分からない | `media_ambiguous` |

**入れ物の id の照合**: 状態の答えの `id` が作った入れ物の id と同じでなければ断る（別の入れ物の状態で
公開に進まない）。**公開の URL の照合**: Meta に渡す `image_url` は relay の origin（`https://thth.me` か
loopback の試験先）の `/m/` だけ。
"""
from __future__ import annotations

import math
import time
import urllib.error

from . import base
from . import facebook as fb
from .. import accounts, jst, media, media_relay
from .. import relay as signed_relay
from ..meta import errors as meta_errors

# 画像（**L2**: content-publishing「JPEG・8MB・4:5〜1.91:1・幅 320〜1440」）。
FORMATS = ("jpeg",)
MAX_BYTES = 8_000_000
RATIO_MIN = 4 / 5
RATIO_MAX = 1.91
WIDTH_MIN = 320
WIDTH_MAX = 1440
CAROUSEL_MAX = 20
ALT_MAX = 1000
POST_OPTIONS = frozenset({"is_ai_generated"})
# 作成の POST は Meta が画像を取りに来る間開いている（Threads と同じ理由・`threads_media.CREATE_TIMEOUT_SECONDS`）。
CREATE_TIMEOUT_SECONDS = 90.0


def require(ok, reason):
    if not ok:
        raise media.MediaError(reason)


def _shape(row):
    w, h = row.get("width"), row.get("height")
    if row.get("orientation") in (5, 6, 7, 8):
        w, h = h, w
    return w, h


def intent_error(manifest):
    """要求を 1 本も出す前に、この添付が Instagram に載るかだけを答える（`media_delivery.error_for`）。"""
    if manifest["captions"]:
        return "unsupported_attachment: instagram/captions"
    if manifest["attachments"]:
        return "unsupported_attachment: instagram/typed_attachment"
    if set(manifest["post_options"]) - POST_OPTIONS:
        return "unsupported_attachment: instagram/post_options"
    rows = manifest["files"]
    if not rows:
        return "unsupported_attachment: instagram/no_media"
    if len(rows) > CAROUSEL_MAX:
        return f"media_limit_exceeded: count ({CAROUSEL_MAX})"
    for row in rows:
        if row["role"] != "media":
            return "unsupported_attachment: instagram/thumbnail"
        if row["kind"] != "image" or row["format"] not in FORMATS:
            return "unsupported_attachment: instagram/" + str(row["format"])
        if type(row["public_size"]) is not int or row["public_size"] < 1:
            return "media_size_unavailable"
        if row["public_size"] > MAX_BYTES:
            return f"media_limit_exceeded: bytes (SI {MAX_BYTES})"
        w, h = _shape(row)
        if type(w) is not int or type(h) is not int or w <= 0 or h <= 0:
            return "media_dimensions_unavailable"
        ratio = w / h
        if ratio < RATIO_MIN - 1e-9 or ratio > RATIO_MAX + 1e-9:
            return "media_limit_exceeded: aspect_ratio (4:5〜1.91:1)"
        if isinstance(row.get("alt"), str) and len(row["alt"]) > ALT_MAX:
            return f"media_limit_exceeded: alt_text ({ALT_MAX})"
    return None


def notes(manifest):
    """lint の注意（幅は Meta が縮める・広げる・L2）。"""
    out = []
    for row in manifest["files"]:
        w, _h = _shape(row)
        if type(w) is int and not WIDTH_MIN <= w <= WIDTH_MAX:
            out.append(f"warning: instagram scales image width outside {WIDTH_MIN}〜{WIDTH_MAX}")
    return out


def _identifier(value):
    found = value.get("id") if isinstance(value, dict) else None
    require(isinstance(found, str) and found.isascii() and found.isdecimal(), "media_response_invalid: id")
    return found


def _grant_url(grant):
    """Meta に渡す公開の URL。**relay の origin の `/m/` だけ**（別の宛先の URL を Meta に取りに行かせない）。"""
    url = grant.get("url") if isinstance(grant, dict) else None
    require(isinstance(url, str) and url.startswith(media_relay.origin() + "/m/"), "media_relay_url_invalid")
    return url


def publish(adapter, post, *, before_publish=None, on_container_created=None):
    phase = "preflight"
    ids: list = []
    grants: list = []
    relay = post.media_relay
    progress = post.media_progress
    ts = jst.iso()
    vetoed = False

    def record(value, **fields):
        nonlocal phase
        phase = value
        progress(value, remote_ids=list(ids), **fields)

    def veto():
        nonlocal vetoed
        for item in post.media_files:
            item.verify()
        if before_publish:
            reason = before_publish()
            if reason:
                vetoed = True
                raise media.MediaError(str(reason))
        require(all(time.time() * 1000 < g["expires_at"] for g in grants), "media_provider_url_expired")

    def create(params):
        return _identifier(adapter._request("POST", f"/{adapter.ig_user_id}/media", form=params,
                                            timeout=CREATE_TIMEOUT_SECONDS))

    def wait(container):
        """`FINISHED` まで待つ（1 分に 1 回・5 分まで・L2）。答えの `id` が入れ物と同じことを見る。"""
        interval = getattr(adapter, "poll_interval", 60.0)
        total = getattr(adapter, "poll_total", 300.0)
        require(all(isinstance(v, (int, float)) and math.isfinite(v) and v >= 0 for v in (interval, total)),
                "media_wait_seconds_invalid")
        deadline = time.monotonic() + total
        while True:
            veto()
            value = adapter._request("GET", f"/{container}", params={"fields": "id,status_code"})
            require(isinstance(value, dict) and value.get("id") == container, "media_container_id_mismatch")
            status = value.get("status_code")
            if status == "FINISHED":
                return
            require(status == "IN_PROGRESS",
                    "media_container_" + (status.lower() if status in ("ERROR", "EXPIRED", "PUBLISHED") else "invalid"))
            if time.monotonic() + interval > deadline:
                raise media.MediaError("media_processing_timeout")
            time.sleep(interval)

    try:
        require(callable(progress) and relay is not None, "media_journal_required")
        reason = intent_error(post.media_manifest)
        require(reason is None, reason or "")
        rows = post.media_manifest["files"]
        require(len(post.media_files) == len(rows)
                and all(item.manifest == row for item, row in zip(post.media_files, rows)),
                "media_prepared_mismatch")
        require(not post.reply_to, "unsupported_attachment: instagram/reply_media")
        require(isinstance(adapter.ig_user_id, str) and adapter.ig_user_id.isascii()
                and adapter.ig_user_id.isdecimal(), "media_account_id_invalid")
        # 1 日の公開の上限は **API の値**で見る（`content_publishing_limit`・本番 10/29 に 100）。届いていれば出さない。
        limit = adapter.publishing_limit()
        require(limit["usage"] < limit["total"],
                f"instagram_publish_limit_reached: 24 時間の公開の上限 {limit['total']} 本に届いています"
                f"（使用 {limit['usage']}）。窓が空くまで出しません")
        options = post.media_manifest["post_options"]
        common = {"caption": post.text}
        if options.get("is_ai_generated") is True:
            common["is_ai_generated"] = "true"
        carousel = len(post.media_files) > 1
        veto()
        for index, item in enumerate(post.media_files):
            record("uploading", index=index)
            source = relay.upload_prepared(item)
            veto()
            record("granting", index=index)
            grant = relay.grant(item, source)
            grants.append(grant)
            url = _grant_url(grant)
            veto()
            record("creating", index=index)
            params = {"image_url": url}
            alt = item.manifest.get("alt")
            if isinstance(alt, str) and alt.strip():
                params["alt_text"] = alt
            if carousel:
                params["is_carousel_item"] = "true"
            else:
                params.update(common)
            container = create(params)
            ids.append(container)
            record("processing", index=index)
            wait(container)
            record("ready", index=index)
        container = ids[0]
        if carousel:
            veto()
            record("creating_carousel")
            container = create({"media_type": "CAROUSEL", "children": ",".join(ids), **common})
            ids.append(container)
            record("processing_carousel", container_id=container)
            wait(container)
            record("ready", container_id=container)
        if on_container_created:
            on_container_created(container)
        veto()
        record("publishing", container_id=container)
        result = _identifier(adapter._request("POST", f"/{adapter.ig_user_id}/media_publish",
                                              form={"creation_id": container},
                                              timeout=fb.PUBLISH_TIMEOUT_SECONDS))
        record("published", post_id=result, publication_ack="pending")
        acknowledged = True
        for grant in grants:
            try:
                require(relay.result(grant, published=True).get("status") == "acknowledged", "media_ack_unconfirmed")
            except (OSError, ValueError, signed_relay.RelayError, accounts.AccountStopped):
                acknowledged = False
        try:
            record("published", post_id=result, publication_ack="acknowledged" if acknowledged else "unconfirmed")
        except (OSError, ValueError, RuntimeError, accounts.AccountStopped):
            pass
        url = None
        try:
            body = adapter._request("GET", f"/{result}", params={"fields": "permalink"})
            url = body.get("permalink") if isinstance(body, dict) and isinstance(body.get("permalink"), str) else None
        except Exception:   # noqa: BLE001 — 出たことは分かっている。URL が引けないだけ
            url = None
        main = post.media_files
        return base.PublishResult(result, url, ts, media=[
            {"sha256": item.manifest["public_sha256"], "kind": item.manifest["kind"],
             "alt_present": base.alt_present(item.manifest), "remote_id": remote}
            for item, remote in zip(main, ids)])
    except accounts.AccountStopped:
        failure = ("media_held" if ids and phase not in ("publishing", "published")
                   else "media_ambiguous" if phase not in ("preflight",) and ids else "publish_vetoed")
        _retire(relay, grants, failure)
        return base.PublishResult(None, None, ts, error="account_stopped", failure=failure)
    except meta_errors.RateLimited as exc:
        # 量の制限: Meta が断った・待ちの間で投げなかった。**その要求は通っていない。**
        failure = "media_held" if ids else "publish_definite"
        _retire(relay, grants, failure)
        _note(record, progress, "held" if ids else "failed", "meta_rate_limited")
        return base.PublishResult(None, None, ts, error=adapter._scrub(exc)[:300], failure=failure)
    except (OSError, ValueError, RuntimeError, urllib.error.URLError, signed_relay.RelayError) as exc:
        http = isinstance(exc, urllib.error.HTTPError)
        definite = http and 400 <= exc.code < 500
        timed_out = str(exc) == "media_processing_timeout"
        not_out = (isinstance(exc, media.MediaError) and not timed_out) or definite
        if http:
            err = fb.graph_error(exc)
            permission = definite and (fb.is_permission_error(err) or err.get("code") == fb._INVALID_TOKEN_CODE)
            reason = f"media_{phase}_http_{exc.code}"
            if definite and isinstance(err.get("code"), int):
                reason += f"_code_{err['code']}"
        else:
            permission = False
            reason = (str(exc) if isinstance(exc, media.MediaError)
                      else "media_relay_failed" if isinstance(exc, (media_relay.MediaRelayError, signed_relay.RelayError))
                      else f"media_{phase}_failed")
        if phase == "preflight" or (not ids and not_out):
            failure = "permission" if permission else "publish_vetoed" if vetoed else "publish_definite"
            state = "failed"
        elif timed_out or not not_out:
            # 5 分で FINISHED にならない・作成や公開の時間切れや 5xx は「分からない」。
            failure, state = "media_ambiguous", "unknown"
        else:
            failure, state = ("permission" if permission else "media_held"), "held"
        _retire(relay, grants, failure)
        _note(record, progress, state, reason)
        if permission:
            reason = adapter._scrub(base.not_granted_message(adapter.PUBLISH_PERMISSION, reason)
                                    .replace("<account>", adapter.auth_account))
        return base.PublishResult(None, None, ts, error=reason, failure=failure)


def _retire(relay, grants, failure):
    """出ていないと分かったときだけ、貸した URL を 1 回ずつ取り消す（分からないときは残す）。"""
    if failure in ("media_ambiguous",) or relay is None:
        return
    for grant in grants:
        try:
            relay.result(grant, published=False)
        except (OSError, ValueError, signed_relay.RelayError, accounts.AccountStopped):
            pass


def _note(record, progress, state, reason):
    try:
        if callable(progress):
            record(state, reason=reason)
    except (OSError, ValueError, RuntimeError, accounts.AccountStopped):
        pass
