"""確かめの結果の記録（5.23.0）: `verify`・`export --check` を打つたびに 1 行（日時・台帳・出し口・緑か赤・赤の語・封の世代・
秒・器の版）。台帳の本文は入れない。置き場は `$THTH_ROOT/register_runs/<年-月>.jsonl`（0600）。

**書けないときに断らない**（確かめの rc を変えない）。読むだけの命令が記録の置き場のせいで赤になると、それ自体が配信の
止まりになるので。CI の箱の記録は箱と一緒に消えるので、数えられるのは手元で打った分だけ。
"""
from __future__ import annotations

import json
import os

from .. import accounts, jst

WORDS_MAX = 20


def _version() -> str:
    """器の版（`thth/VERSION`。台帳の機械は `thth` の頭を import しない決まりなので、ファイルから読む）。"""
    try:
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "VERSION"),
                  encoding="utf-8") as f:
            return f.read().strip()[:32]
    except OSError:
        return "unknown"


def runs_dir() -> str:
    return os.path.join(accounts.thth_root(), "register_runs")


def _words(value: dict) -> list:
    words = [p.get("word") for p in (value.get("problems") or []) if isinstance(p, dict)]
    for name, one in (value.get("outlets") or {}).items():
        if isinstance(one, dict):
            words += [f"{name}:{p.get('word')}" for p in (one.get("problems") or []) if isinstance(p, dict)]
            if one.get("code"):
                words.append(f"{name}:{one['code']}")
            words += [f"{name}:{w}" for w in (one.get("found") or [])]
    words += list(value.get("found") or [])
    if value.get("code"):
        words.append(value["code"])
    return [w for w in words if isinstance(w, str)][:WORDS_MAX]


def note(command: str, register, outlet, value: dict | None, ok: bool, seconds: float) -> None:
    """1 行を足す。どんな失敗も飲み込む（確かめそのものを壊さない）。"""
    try:
        value = value or {}
        line = {"at": jst.now_jst().isoformat(timespec="seconds"), "command": command, "register": str(register),
                "outlet": outlet, "ok": bool(ok), "words": _words(value), "generation": value.get("generation"),
                "seconds": round(float(seconds), 3), "version": _version()}
        folder = runs_dir()
        os.makedirs(folder, mode=0o700, exist_ok=True)
        path = os.path.join(folder, jst.now_jst().strftime("%Y-%m") + ".jsonl")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception:  # noqa: BLE001 — 記録は付け足し。確かめの rc を変えない
        pass


def read(limit: int = 1000) -> list:
    """今月と先月の記録（新しい順ではなく書いた順）。読めない行は飛ばす。"""
    out = []
    try:
        names = sorted(n for n in os.listdir(runs_dir()) if n.endswith(".jsonl"))[-2:]
    except OSError:
        return out
    for name in names:
        try:
            with open(os.path.join(runs_dir(), name), encoding="utf-8") as f:
                for raw in f:
                    try:
                        out.append(json.loads(raw))
                    except ValueError:
                        continue
        except OSError:
            continue
    return out[-limit:]
