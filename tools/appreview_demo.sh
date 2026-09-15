#!/bin/bash
# App Review の録画（2026-09-15）。**実演の進行と、その録画**を 1 本でやる。
#
#   bash tools/appreview_demo.sh kopicha-threads
#
# 流れ: 窓を整える → 3 秒の試し撮り → 本番の録画を始める → auth（同意画面）
#       → doctor → search → mentions → profile → location → 録画を止める → MP4。
#
# 設計の要点は 1 つ: **録画が始められなくても実演は止めない。** 前の版
# （Codex・AppleScript＋Swift＋screencapture＋ffmpeg）は試し撮りの寸法検査で
# 自分を止め、一度も本番に届かなかった。ここでは録画に失敗したら「QuickTime で
# 録ってください」と言って実演を続ける。録画はあくまで従で、主は実演。
#
# Codex 版から取り入れたもの: screencapture を背景で回して SIGINT で止める・
# 止まったことを確認してから次へ・試し撮り・ブラウザの窓も同じ枠へ・MP4 変換。
# 外したもの: 窓の位置を取るだけの Swift（osascript の bounds で同じ値が取れる）、
# 録画の都合で実演を中断する分岐。
#
# 前提: Terminal.app で実行（窓の調整と録画範囲の取得に使う）。等倍のモニタなら
# 1920×1080 の窓 ＝ 1920×1080 の動画（Meta の要求「1080 以上」）。Retina なら 2 倍。
# 画面収録の許可を初めて求められたら、許可してターミナルを開き直し、もう一度。
set -u
umask 077

ACCOUNT="${1:-}"
THTH="${THTH_BIN:-$HOME/.local/bin/thth}"
SCREENCAPTURE="${SCREENCAPTURE_BIN:-/usr/sbin/screencapture}"
FFMPEG="${FFMPEG_BIN:-/opt/homebrew/bin/ffmpeg}"
FFPROBE="${FFPROBE_BIN:-/opt/homebrew/bin/ffprobe}"
WIN_W="${DEMO_W:-1920}"
WIN_H="${DEMO_H:-1080}"
MENUBAR=25
RECORD="${DEMO_RECORD:-1}"          # 0 で自動録画をしない（QuickTime で録る）
OUT="${DEMO_OUT:-$HOME/Movies/thth-appreview-$(date +%Y%m%d-%H%M%S)}"

if [ -z "$ACCOUNT" ]; then
  echo "使い方: bash tools/appreview_demo.sh <account>（例: kopicha-threads）" >&2
  exit 2
fi
if [ ! -x "$THTH" ]; then
  echo "thth が見つかりません: $THTH（THTH_BIN で場所を渡せます）" >&2
  exit 2
fi
mkdir -p "$OUT" || exit 2

# ---------------------------------------------------------------- 録画 ----
REC_PID=''

start_recording() {              # $1 = 出力 .mov  $2 = 上限秒（保険。INT が届かなくても止まる）
  # **ジョブ制御を一時的に入れる。** 非対話のスクリプトが `&` で起こした子は SIGINT を
  # 無視する（bash の仕様）ので、そのままだと後で `kill -INT` が効かず録画が止まらない。
  # `set -m` の下では子が自分のプロセスグループを持ち、INT が届く。
  set -m
  "$SCREENCAPTURE" -R"$REGION" -v -V"$2" "$1" 2>"$1.log" &
  REC_PID=$!
  set +m
  sleep 3
  if ! kill -0 "$REC_PID" 2>/dev/null; then
    wait "$REC_PID" 2>/dev/null; REC_PID=''
    return 1
  fi
  return 0
}

stop_recording() {
  [ -n "$REC_PID" ] || return 0
  kill -INT "$REC_PID" 2>/dev/null || true
  for _ in $(seq 1 150); do
    if ! kill -0 "$REC_PID" 2>/dev/null; then
      wait "$REC_PID" 2>/dev/null || true; REC_PID=''; return 0
    fi
    sleep 0.1
  done
  printf '\n録画の停止を確認できません。メニューバーの停止ボタンで止めてください（%s）。\n' "$OUT"
  REC_PID=''
  return 1
}

video_size() {                   # $1 = 動画 → "WxH" か空
  [ -x "$FFPROBE" ] || return 0
  "$FFPROBE" -v error -select_streams v:0 -show_entries stream=width,height \
    -of csv=s=x:p=0 "$1" 2>/dev/null
}

trap 'stop_recording; exit 130' INT TERM
trap 'stop_recording' EXIT

# ---------------------------------------------------------------- 進行 ----
pause() {                        # Return を待つ（$2 秒で自動で進む・省略時は待ち続ける）
  printf '\n'
  if [ -n "${2:-}" ]; then
    read -r -t "$2" -p "$1" _ </dev/tty || true
  else
    read -r -p "$1" _ </dev/tty
  fi
}

banner() {
  printf '\033[3J\033[H\033[2J'
  printf 'THTH — Threads App Review demonstration\n'
  printf 'Permission: %s\n' "$1"
  printf 'Account: %s   Access level: standard (before review)\n' "$ACCOUNT"
  printf '%s\n\n' "----------------------------------------------------------------"
}

step() {                         # 見出し → コマンド表示 → 実行 → exit → 8 秒（か Return）で次へ
  permission="$1"; shift
  banner "$permission"
  printf '$ thth'
  for arg in "$@"; do printf ' %q' "$arg"; done
  printf '\n\n'
  sleep 2
  "$THTH" "$@"
  rc=$?
  printf '\n[exit %s]\n' "$rc"
  printf '%s\t%s\n' "$permission" "$rc" >> "$OUT/results.tsv"
  pause '  [8 秒後に次へ・急ぐなら Return] ' 8
}

# ---------------------------------------------------------------- 窓 ----
printf '\033[3J\033[H\033[2J'
cat <<EOF
THTH — App Review の録画
アカウント: $ACCOUNT
保存先:     $OUT

この順に進みます。
  1. thth auth        … 認可 URL → ブラウザで同意画面 → 戻り URL を貼る
  2. thth doctor      … トークンに乗った権限の一覧
  3. topics --search  … threads_keyword_search
  4. mentions         … threads_manage_mentions（事前に別アカウントから @ 言及を 1 本）
  5. profile threads  … threads_profile_discovery（標準アクセスは Meta 公式のみ）
  6. location search  … threads_location_tagging（標準アクセスは "Menlo Park" のみ）

通知を切り、関係ないウィンドウを閉じてください。音声は録りません。
EOF

REGION="${DEMO_REGION:-}"        # 試験用: x,y,w,h を渡すと窓の調整を飛ばす
if [ -n "$REGION" ]; then
  :
elif [ "${TERM_PROGRAM:-}" = "Apple_Terminal" ]; then
  /usr/bin/osascript >/dev/null 2>&1 <<APPLE || true
tell application id "com.apple.Terminal"
  set font size of selected tab of front window to 18
  set bounds of front window to {0, $MENUBAR, $WIN_W, $((MENUBAR + WIN_H))}
end tell
APPLE
  # 実際の枠を読み返す（等倍のモニタなら point ＝ pixel）
  BOUNDS="$(/usr/bin/osascript -e 'tell application id "com.apple.Terminal" to get bounds of front window' 2>/dev/null | tr -d ' ')"
  if [[ "$BOUNDS" =~ ^(-?[0-9]+),(-?[0-9]+),(-?[0-9]+),(-?[0-9]+)$ ]]; then
    REGION="${BASH_REMATCH[1]},${BASH_REMATCH[2]},$(( BASH_REMATCH[3] - BASH_REMATCH[1] )),$(( BASH_REMATCH[4] - BASH_REMATCH[2] ))"
  fi
  # ブラウザも同じ枠へ（同意画面が録画に入るように）。開いていなければ何もしない。
  printf '\n認可に使うブラウザ名（Google Chrome / Safari / 空＝調整しない）: '
  read -r BROWSER </dev/tty || BROWSER=''
  if [ -n "$BROWSER" ]; then
    /usr/bin/osascript >/dev/null 2>&1 <<APPLE || printf '（%s の窓は調整できませんでした。手で重ねてください）\n' "$BROWSER"
tell application "$BROWSER"
  if (count of windows) > 0 then set bounds of front window to {0, $MENUBAR, $WIN_W, $((MENUBAR + WIN_H))}
end tell
APPLE
  fi
else
  printf '\nTerminal.app ではないので窓の調整と自動録画はしません（QuickTime で録ってください）。\n'
fi
[ -n "$REGION" ] || RECORD=0
printf '\n録画範囲: %s\n' "${REGION:-（取れませんでした）}"

# ---------------------------------------------------------------- 試し撮り ----
if [ "$RECORD" = 1 ]; then
  if [ ! -x "$SCREENCAPTURE" ]; then
    printf '%s が無いので自動録画はしません。QuickTime で録ってください。\n' "$SCREENCAPTURE"
    RECORD=0
  fi
fi
if [ "$RECORD" = 1 ]; then
  pause '  [Return で 3 秒の試し撮り] '
  if start_recording "$OUT/test.mov" 8 && stop_recording && [ -s "$OUT/test.mov" ]; then
    SIZE="$(video_size "$OUT/test.mov")"
    printf '試し撮り: %s（%s）\n' "$OUT/test.mov" "${SIZE:-寸法は未確認}"
    case "$SIZE" in
      *x*) w="${SIZE%x*}"; h="${SIZE#*x}"
           if [ "$w" -lt 1920 ] || [ "$h" -lt 1080 ]; then
             printf '**1920×1080 より小さい。** Meta は 1080 以上を求めます。窓を広げてやり直すか、このまま続けるかは判断してください。\n'
           fi ;;
    esac
    /usr/bin/open "$OUT/test.mov" 2>/dev/null || true
    pause '  [再生して字が読めたら Return。読めなければ Ctrl-C で中止] '
  else
    printf '\n**自動録画ができませんでした**（%s）。\n' "$OUT/test.mov.log"
    printf '画面収録の許可（システム設定 → プライバシーとセキュリティ → 画面収録 → ターミナル）を確認してください。\n'
    printf 'このまま続けるなら、QuickTime の「画面の一部を収録」でこの窓を囲んでから Return。\n'
    RECORD=0
    pause '  [Return で続ける・Ctrl-C で中止] '
  fi
else
  pause '  [QuickTime の収録を始めてから Return] '
fi

# ---------------------------------------------------------------- 本番 ----
if [ "$RECORD" = 1 ]; then
  if ! start_recording "$OUT/review.mov" 1200; then    # 最長 20 分で自動停止
    printf '\n**本番の録画が始められませんでした。** QuickTime で録ってから Return。\n'
    RECORD=0
    pause '  [Return で続ける] '
  fi
fi
: > "$OUT/results.tsv"

# 1. 認可（同意画面を見せる。戻り URL に一度きりの code が映るが、貼った瞬間に使い切る）
banner "login and consent (all requested permissions)"
printf '$ thth auth %q\n\n' "$ACCOUNT"
sleep 2
"$THTH" auth "$ACCOUNT"
rc=$?
printf '\n[exit %s]\n' "$rc"
printf 'auth\t%s\n' "$rc" >> "$OUT/results.tsv"
pause '  [ブラウザを後ろに回して Return] '

# 2. 何が乗ったか
step "granted scopes (threads_basic + /debug_token)" doctor "$ACCOUNT"

# 3〜6. 申請する 4 権限
step "threads_keyword_search"     topics "$ACCOUNT" --search "コーヒー"
step "threads_manage_mentions"    mentions "$ACCOUNT"
step "threads_profile_discovery"  profile "$ACCOUNT" threads
step "threads_location_tagging"   location search "$ACCOUNT" "Menlo Park"

banner "end of demonstration"
sleep 2

# ---------------------------------------------------------------- 仕上げ ----
if [ "$RECORD" = 1 ]; then
  stop_recording || true
  if [ -s "$OUT/review.mov" ]; then
    SIZE="$(video_size "$OUT/review.mov")"
    printf '録画: %s（%s）\n' "$OUT/review.mov" "${SIZE:-寸法は未確認}"
    if [ -x "$FFMPEG" ]; then
      printf 'MP4 に変換しています…\n'
      if "$FFMPEG" -nostdin -v error -i "$OUT/review.mov" -map 0:v:0 -an \
           -vf 'scale=trunc(iw/2)*2:trunc(ih/2)*2' -c:v libx264 -crf 18 -pix_fmt yuv420p \
           -movflags +faststart "$OUT/review.mp4"; then
        printf '提出用: %s\n' "$OUT/review.mp4"
      else
        printf '変換に失敗。.mov のまま提出できます: %s\n' "$OUT/review.mov"
      fi
    fi
  else
    printf '録画ファイルが空です（%s）。QuickTime で録っていればそちらを使ってください。\n' "$OUT/review.mov.log"
  fi
else
  printf 'QuickTime の収録を止めて保存してください。\n'
fi
printf '\n結果: %s/results.tsv\n' "$OUT"
cat "$OUT/results.tsv"
printf '\n提出前に動画を通しで見て、4 権限それぞれの場面と同意画面が映っていることを確かめてください。\n'
