"""括りの段 3: 読むだけの命令を、鍵で括りの中に通す通り道（VM の受け口）。

設計: docs/設計_THTHアカウント（括り）_叩き台_2026-11-26.md §5.2「読むだけの命令は 1 つの通り道でまとめて通す」。
命令の名前と引数を VM に渡し、VM が括りの中で同じ命令を打って答えを返す。1 つずつ作り直さない。

開くのは **括りとレーンが書かれた鍵（資格の行の `workspace` と `lane`）だけ**。今の口座の鍵（招待で入った
外の人の鍵を含む）は口座 1 つにしか結び付いていないので、括り全体を読むこの道には入れない。
Worker が括りの単位で頼みを束ねる所（括りの束）は Meta の審査のあと（008acdae）なので、今は受け口だけ。

通してよい命令と旗は `PASS` の表だけ。目印を付ける・書く・手元の道を取る旗は表に無いので断る。
値は英数字と `. : + - _ ,` だけ（`/` が無い＝道を取らせない）。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass, field

from . import command_scope

VALUE = re.compile(r"[A-Za-z0-9_.:+,-]{1,64}\Z")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
ARGV_MAX = 24
OUT_MAX = 60000
TIMEOUT = 120


@dataclass(frozen=True)
class Spec:
    values: frozenset = frozenset()     # 値を 1 つ取る旗
    flags: frozenset = frozenset()      # 値を取らない旗
    positionals: tuple = (0, 0)         # 名前（口座・project）の数の (最小, 最大)
    forced: tuple = ()                  # 必ず足す旗（読むだけにするため）
    choices: dict = field(default_factory=dict)


PASS: dict[str, Spec] = {
    "board": Spec(flags=frozenset({"--json"})),
    "morning": Spec(flags=frozenset({"--json", "--no-world", "--counts-only"}), positionals=(1, 1),
                    forced=("--no-mark",)),
    "observe": Spec(flags=frozenset({"--json", "--no-world", "--counts-only"}), positionals=(1, 1),
                    forced=("--no-mark",)),
    "schedule": Spec(values=frozenset({"--days", "--window"}), flags=frozenset({"--unapproved", "--json"}),
                     positionals=(0, 1)),
    "where-log": Spec(values=frozenset({"--since", "--medium"}), flags=frozenset({"--json"}),
                      choices={"--medium": {"threads", "bluesky", "mastodon", "x"}}),
    "handoff-report": Spec(values=frozenset({"--project"}), flags=frozenset({"--json"}), positionals=(0, 1)),
    "analytics-report": Spec(values=frozenset({"--project", "--window-days", "--min-n", "--by", "--weeks",
                                               "--since"}),
                             flags=frozenset({"--compare-previous", "--weekly-goals", "--per-post-clicks",
                                              "--json"}),
                             choices={"--by": {"kind", "hour_band", "topic", "tag", "attachment_kind", "goal"}}),
    "unanswered": Spec(values=frozenset({"--since"}), flags=frozenset({"--json"}), positionals=(1, 1)),
}


class PassRefused(ValueError):
    pass


def build_argv(argv) -> list[str]:
    """頼まれた argv を表で確かめ、打つ argv を返す。表に無い物は `pass_unsupported`。"""
    if type(argv) is not list or not argv or len(argv) > ARGV_MAX or any(type(a) is not str for a in argv):
        raise PassRefused("invalid_request")
    command, rest = argv[0], argv[1:]
    spec = PASS.get(command)
    if spec is None or command_scope.scope_of(command) != command_scope.WS:
        raise PassRefused("pass_unsupported")
    out = [command]
    names = []
    i = 0
    while i < len(rest):
        token = rest[i]
        if token.startswith("--"):
            flag, eq, inline = token.partition("=")
            if flag in spec.flags and not eq:
                out.append(flag)
                i += 1
                continue
            if flag in spec.values:
                if eq:
                    value = inline
                    i += 1
                else:
                    if i + 1 >= len(rest):
                        raise PassRefused("invalid_request")
                    value = rest[i + 1]
                    i += 2
                if not VALUE.fullmatch(value) or value.startswith("-") and flag != "--window":
                    raise PassRefused("invalid_request")
                if flag in spec.choices and value not in spec.choices[flag]:
                    raise PassRefused("invalid_request")
                out += [flag, value]
                continue
            raise PassRefused("pass_unsupported")
        if token.startswith("-") or not NAME.fullmatch(token):
            raise PassRefused("invalid_request")
        names.append(token)
        i += 1
    low, high = spec.positionals
    if not low <= len(names) <= high:
        raise PassRefused("invalid_request")
    return out + list(spec.forced) + names


def execute(context, request) -> dict:
    """`{"operation": "ws_command", "account": …, "argv": [...]}` を括りの中で打つ。"""
    from .report_service import ReportServiceError
    if getattr(context, "workspace", None) is None or getattr(context, "lane", None) is None:
        # 括りとレーンの無い鍵（口座の鍵）は入れない。無い口と同じに断る。
        raise ReportServiceError("unsupported_operation")
    if type(request) is not dict or set(request) - {"operation", "account", "argv"}:
        raise ReportServiceError("invalid_request")
    try:
        argv = build_argv(request.get("argv"))
    except PassRefused as exc:
        raise ReportServiceError(str(exc)) from None
    env = {k: v for k, v in os.environ.items() if not k.startswith(("THTH_WS", "THTH_ACCOUNTS_DIR", "THTH_LANE",
                                                                      "THTH_REMOTE_CONFIG"))}
    env.update(THTH_WS=context.workspace, THTH_ACTOR=f"lane:{context.lane}", NO_COLOR="1")
    try:
        proc = subprocess.run([sys.executable, "-m", "thth", *argv], capture_output=True, text=True,
                              timeout=TIMEOUT, env=env, stdin=subprocess.DEVNULL,
                              cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    except subprocess.TimeoutExpired:
        raise ReportServiceError("upstream_unavailable") from None
    return {"command": argv[0], "rc": proc.returncode, "stdout": proc.stdout[:OUT_MAX],
            "stderr": proc.stderr[:OUT_MAX // 6], "truncated": len(proc.stdout) > OUT_MAX}
