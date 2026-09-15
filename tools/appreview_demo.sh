#!/bin/bash
# App Review の録画の進行役（2026-09-15）。**録画はしない。** 画面の窓を整え、
# 権限ごとに「見出し → コマンド表示 → Return → 実行 → Return」を進めるだけ。
#
# なぜ録画を持たないか。前の版（AppleScript＋Swift＋screencapture＋ffmpeg）は
# 試し撮りが 598×386・1280×720 になって自分の検査で止まり、一度も本番に届かなかった。
# 録画は QuickTime（画面の一部を収録・マイク無し）で人がやる方が確実で、
# 何が映ったかもその場で見える。スクリプトは進行だけを持つ。
#
# 使い方（Terminal.app で）:
#   bash tools/appreview_demo.sh kopicha-threads
#
# 前提: ~/.local/bin/thth（VM を呼ぶラッパ）。等倍のモニタなら、窓を 1920×1080 に
# すると動画もそのサイズになる（Meta の要求「1080 以上」を満たす）。Retina なら 2 倍。
set -u

ACCOUNT="${1:-}"
THTH="${THTH_BIN:-$HOME/.local/bin/thth}"
WIN_W="${DEMO_W:-1920}"
WIN_H="${DEMO_H:-1080}"
MENUBAR=25

if [ -z "$ACCOUNT" ]; then
  echo "使い方: bash tools/appreview_demo.sh <account>（例: kopicha-threads）" >&2
  exit 2
fi
if [ ! -x "$THTH" ]; then
  echo "thth が見つかりません: $THTH（THTH_BIN で場所を渡せます）" >&2
  exit 2
fi

# 窓を左上に寄せて指定サイズに（Terminal.app のときだけ。失敗しても続ける）。
if [ "${TERM_PROGRAM:-}" = "Apple_Terminal" ]; then
  /usr/bin/osascript -e "tell application id \"com.apple.Terminal\" to set bounds of front window to {0, $MENUBAR, $WIN_W, $((MENUBAR + WIN_H))}" >/dev/null 2>&1 || true
  /usr/bin/osascript -e 'tell application id "com.apple.Terminal" to set font size of selected tab of front window to 18' >/dev/null 2>&1 || true
fi

pause() {
  printf '\n'
  read -r -p "$1" _ </dev/tty
}

banner() {
  clear
  printf 'THTH — Threads App Review demonstration\n'
  printf 'Permission: %s\n' "$1"
  printf 'Account: %s   Access level: standard (before review)\n' "$ACCOUNT"
  printf '%s\n\n' "----------------------------------------------------------------"
}

# 見出し → コマンドを表示 → Return で実行 → 終了コードを表示 → Return で次へ
step() {
  permission="$1"; shift
  banner "$permission"
  printf '$ thth'
  for arg in "$@"; do printf ' %q' "$arg"; done
  printf '\n'
  pause '  [Return で実行] '
  printf '\n'
  "$THTH" "$@"
  rc=$?
  printf '\n[exit %s]\n' "$rc"
  pause '  [Return で次へ] '
}

clear
cat <<EOF
THTH — App Review の録画の進行役
アカウント: $ACCOUNT
窓: ${WIN_W}x${WIN_H}（左上）

この順に進みます。各段は Return で進みます。
  1. thth auth        … 認可 URL → ブラウザで 4 権限の同意画面 → 戻り URL を貼る
  2. thth doctor      … トークンに乗った権限の一覧
  3. topics --search  … threads_keyword_search
  4. mentions         … threads_manage_mentions（事前に別アカウントから @ 言及を 1 本）
  5. profile threads  … threads_profile_discovery（標準アクセスは Meta 公式のみ）
  6. location search  … threads_location_tagging（標準アクセスは "Menlo Park" のみ）

いま QuickTime で「新規画面収録 → 画面の一部を収録」を選び、この窓を囲んで
収録を始めてください（マイクは「なし」）。ブラウザもこの窓の範囲に重ねて操作します。
EOF
pause '  [収録が始まったら Return] '

# 1. 認可（同意画面を見せる。戻り URL に一度きりの code が映るが、貼った瞬間に使い切る）
banner "login and consent (all requested permissions)"
printf '$ thth auth %q\n' "$ACCOUNT"
pause '  [Return で実行] '
printf '\n'
"$THTH" auth "$ACCOUNT"
printf '\n[exit %s]\n' "$?"
pause '  [Return で次へ] '

# 2. 何が乗ったか
step "granted scopes (threads_basic + /debug_token)" doctor "$ACCOUNT"

# 3〜6. 申請する 4 権限
step "threads_keyword_search"     topics "$ACCOUNT" --search "コーヒー"
step "threads_manage_mentions"    mentions "$ACCOUNT"
step "threads_profile_discovery"  profile "$ACCOUNT" threads
step "threads_location_tagging"   location search "$ACCOUNT" "Menlo Park"

clear
printf 'THTH — end of demonstration\n\nQuickTime の収録を止めて、動画を確認してください。\n'
