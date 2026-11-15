"""原稿の頭の `seeds:` を台帳と突き合わせて数える（読むだけ・5.23.0）。

`seeds:` は原稿が使った台帳の記録の番号（`種類:番号` を空白区切り・`queuefile.RESERVED_USER_KEYS`）。2 つの媒体で同じ
数え方にする: 番号を書いた原稿の本数・種類ごとの本数・台帳に無い番号・まだ 1 度も種になっていない記録の数（種類ごと）。
`common:` で始まる番号は共通の台帳の物として数えるだけ（確かめない）。

**lint の赤にも承認の指紋にも入れない**（投稿の道を変えない）。原稿は書き換えない。
"""
from __future__ import annotations

import os

from .. import queuefile
from . import vessel as V
from .errors import RegisterRefused

FILES_MAX = 20_000
SHOWN = 50


def _seeds_of(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read(256 * 1024)
    except (OSError, UnicodeDecodeError):
        return None
    split = queuefile._split_front_matter(text)
    if split is None:
        return None
    try:
        fm, _dup = queuefile._parse_kv(split[0])
    except Exception:  # noqa: BLE001 — 読めない頭の原稿は数えない（原稿の検査は lint の仕事）
        return None
    value = fm.get("seeds")
    return str(value).split() if value not in (None, "") else []


def count(root: str, queue: str) -> dict:
    if not isinstance(queue, str) or not os.path.isdir(queue) or os.path.islink(queue):
        raise RegisterRefused("register_args_invalid", "queue")
    vessel = V.open_vessel(root)
    files, seeded, by_kind, missing, common, used = 0, 0, {}, [], set(), set()
    for here, dirs, names in os.walk(queue, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for name in sorted(names):
            if not name.endswith(".md"):
                continue
            files += 1
            if files > FILES_MAX:
                raise RegisterRefused("register_folder_too_wide", "files")
            seeds = _seeds_of(os.path.join(here, name))
            if not seeds:
                continue
            seeded += 1
            rel = os.path.relpath(os.path.join(here, name), queue).replace(os.sep, "/")
            kinds = set()
            for ref in seeds:
                if ref.startswith(V.COMMON_REF):
                    common.add(ref)
                    kinds.add("common")
                    continue
                kind = ref.split(":", 1)[0]
                kinds.add(kind)
                if vessel.unique(ref) is None:
                    missing.append({"draft": rel, "seed": ref})
                else:
                    used.add(ref)
            for kind in kinds:
                by_kind[kind] = by_kind.get(kind, 0) + 1
    never = {}
    for rec in vessel.records.values():
        if rec.ref not in used:
            never.setdefault(rec.kind, []).append(rec.ref)
    return {"drafts": files, "seeded": seeded, "by_kind": dict(sorted(by_kind.items())),
            "missing": missing[:SHOWN], "missing_count": len(missing), "common_seeds": len(common),
            "never_seeded": {k: {"count": len(v), "examples": sorted(v)[:10]} for k, v in sorted(never.items())}}
