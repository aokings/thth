#!/bin/bash
# App Review の録画（2026-09-15）。**実演の進行と、その録画**を 1 本でやる。
#
#   bash tools/appreview_demo.sh kopicha-threads
#
# 流れ: 窓を整える → 3 秒の試し撮り → 本番の録画を始める → auth（同意画面）
#       → doctor → search → mentions → profile → location → 録画を止める。
#
# 設計の要点は 2 つ。
#
# 1. **録画が始められなくても実演は止めない。** 録画に失敗したら「QuickTime で
#    録ってください」と言って実演を続ける。録画は従で、主は実演。
#
# 2. **録画は ffmpeg（avfoundation）で、止めるのは標準入力への `q`。** 信号は使わない。
#    この Mac で実測した結果（tools/appreview_rectest.sh・2026-09-15）:
#      - screencapture は `-V` の時間で回すしかなく、INT を送るとファイルを書かずに死ぬ
#      - スクリプトが `&` で起こした ffmpeg は INT も TERM も無視する（孤児が 5 分回った）
#      - 標準入力（名前付きパイプ）に `q` を書くと、背景の ffmpeg が 1 秒で閉じて
#        1920×1080 の MP4 が残る（「Exiting normally」）
#    Codex 版（screencapture を INT で止める）は前提から成り立たなかった。
#
# 前提: Terminal.app で実行（窓の調整と録画範囲に使う）。ffmpeg は /opt/homebrew。
# 画面収録の許可を初めて求められたら、許可してターミナルを開き直し、もう一度。
set -u
umask 077

ACCOUNT="${1:-}"
THTH="${THTH_BIN:-$HOME/.local/bin/thth}"
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
CTL="$OUT/ctl.fifo"
SCREEN=''                        # avfoundation の画面 device の番号
SCALE="${DEMO_SCALE:-}"          # point → pixel の倍率（等倍 1・Retina 2。試し撮りで測る）

screen_device() {                # "Capture screen 0" の番号を返す（無ければ空）
  "$FFMPEG" -hide_banner -f avfoundation -list_devices true -i "" 2>&1 \
    | grep -iE "\] \[[0-9]+\] Capture screen" | head -1 \
    | sed -E 's/.*\[([0-9]+)\] Capture screen.*/\1/'
}

crop_filter() {                  # 録画範囲（point）を pixel に直した crop
  IFS=, read -r x y w h <<<"$REGION"
  s="${SCALE:-1}"
  printf 'crop=%d:%d:%d:%d' "$((w * s))" "$((h * s))" "$((x * s))" "$((y * s))"
}

# ffmpeg 本体を起こす。`exec` で自分自身が ffmpeg になる（`&` で背景に回したとき
# `$!` が ffmpeg の pid になるように。外側に bash が挟まると、止める相手を間違える）。
ffmpeg_run() {                   # $1 = 出力 .mp4  残り = 追加オプション（-t 3 など）
  out="$1"; shift
  exec "$FFMPEG" -hide_banner -y -f avfoundation -framerate 30 -capture_cursor 1 \
    -i "$SCREEN:none" -vf "$(crop_filter)" -c:v h264_videotoolbox -b:v 8M \
    -pix_fmt yuv420p -an "$@" "$out"
}

start_recording() {              # $1 = 出力 .mp4。標準入力は名前付きパイプ（あとで q を書く）
  rm -f "$CTL"; mkfifo "$CTL" || return 1
  exec 3<>"$CTL"                 # 読み書きで開く＝開く側が待たされない
  ffmpeg_run "$1" <&3 2>"$1.log" &
  REC_PID=$!
  sleep 3
  if ! kill -0 "$REC_PID" 2>/dev/null; then
    wait "$REC_PID" 2>/dev/null; REC_PID=''; exec 3>&-
    return 1
  fi
  return 0
}

stop_recording() {               # q を書いて、閉じるのを待つ（30 秒）。だめなら KILL
  [ -n "$REC_PID" ] || return 0
  printf 'q' >&3 2>/dev/null || true
  for _ in $(seq 1 300); do
    if ! kill -0 "$REC_PID" 2>/dev/null; then
      wait "$REC_PID" 2>/dev/null || true; REC_PID=''; exec 3>&-; return 0
    fi
    sleep 0.1
  done
  printf '\n録画が 30 秒たっても閉じません。KILL します（ファイルは壊れます）。\n'
  kill -KILL "$REC_PID" 2>/dev/null || true
  wait "$REC_PID" 2>/dev/null || true; REC_PID=''; exec 3>&-
  return 1
}

video_size() {                   # $1 = 動画 → "W,H,秒" か空
  [ -x "$FFPROBE" ] || return 0
  "$FFPROBE" -v error -select_streams v:0 -show_entries stream=width,height \
    -show_entries format=duration -of csv=p=0 "$1" 2>/dev/null | tr '\n' ' '
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
  BOUNDS="$(/usr/bin/osascript -e 'tell application id "com.apple.Terminal" to get bounds of front window' 2>/dev/null | tr -d ' ')"
  if [[ "$BOUNDS" =~ ^(-?[0-9]+),(-?[0-9]+),(-?[0-9]+),(-?[0-9]+)$ ]]; then
    REGION="${BASH_REMATCH[1]},${BASH_REMATCH[2]},$(( BASH_REMATCH[3] - BASH_REMATCH[1] )),$(( BASH_REMATCH[4] - BASH_REMATCH[2] ))"
  fi
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
  printf '\nTerminal.app ではないので窓の調整はしません。\n'
fi
[ -n "$REGION" ] || RECORD=0
printf '\n録画範囲: %s\n' "${REGION:-（取れませんでした→自動録画なし）}"

# ---------------------------------------------------------------- 試し撮り ----
if [ "$RECORD" = 1 ] && [ ! -x "$FFMPEG" ]; then
  printf '%s が無いので自動録画はしません。QuickTime で録ってください。\n' "$FFMPEG"
  RECORD=0
fi
if [ "$RECORD" = 1 ]; then
  SCREEN="$(screen_device)"
  if [ -z "$SCREEN" ]; then
    printf 'ffmpeg から画面が見えません（画面収録の許可を確認）。QuickTime で録ってください。\n'
    RECORD=0
  fi
fi
if [ "$RECORD" = 1 ]; then
  pause '  [Return で 3 秒の試し撮り] '
  # 前面で 3 秒。ここで画面の pixel 幅も分かる（ログの「Video: rawvideo … 3840x1600」）
  ( ffmpeg_run "$OUT/test.mp4" -t 3 </dev/null 2>"$OUT/test.mp4.log" )
  if [ -z "$SCALE" ]; then
    PIX_W="$(grep -oE 'rawvideo[^,]*, [a-z0-9]+, ([0-9]+)x[0-9]+' "$OUT/test.mp4.log" | head -1 | sed -E 's/.* ([0-9]+)x[0-9]+$/\1/')"
    PT_W="$(/usr/bin/osascript -e 'tell application "Finder" to get bounds of window of desktop' 2>/dev/null | tr -d ' ' | cut -d, -f3)"
    if [ -n "$PIX_W" ] && [ -n "$PT_W" ] && [ "$PT_W" -gt 0 ] && [ "$PIX_W" -ge "$((PT_W * 2))" ]; then
      SCALE=2                    # Retina: point の 2 倍が pixel。crop を 2 倍にして撮り直す
      printf 'Retina（%s px / %s pt）なので範囲を 2 倍にして撮り直します。\n' "$PIX_W" "$PT_W"
      ( ffmpeg_run "$OUT/test.mp4" -t 3 </dev/null 2>"$OUT/test.mp4.log" )
    else
      SCALE=1
    fi
  fi
  if [ -s "$OUT/test.mp4" ]; then
    SIZE="$(video_size "$OUT/test.mp4")"
    printf '試し撮り: %s（%s）\n' "$OUT/test.mp4" "${SIZE:-寸法は未確認}"
    case "$SIZE" in
      *,*) w="${SIZE%%,*}"; rest="${SIZE#*,}"; h="${rest%%,*}"
           if [ "${w:-0}" -lt 1920 ] || [ "${h:-0}" -lt 1080 ]; then
             printf '**1920×1080 より小さい。** Meta は 1080 以上を求めます。窓を広げてやり直すか、このまま続けるかは判断してください。\n'
           fi ;;
    esac
    /usr/bin/open "$OUT/test.mp4" 2>/dev/null || true
    pause '  [再生して字が読めたら Return。読めなければ Ctrl-C で中止] '
  else
    printf '\n**試し撮りができませんでした**（%s）。\n' "$OUT/test.mp4.log"
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
  if ! start_recording "$OUT/review.mp4"; then
    printf '\n**本番の録画が始められませんでした**（%s）。QuickTime で録ってから Return。\n' "$OUT/review.mp4.log"
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
  if stop_recording && [ -s "$OUT/review.mp4" ]; then
    printf '録画: %s（%s）\n' "$OUT/review.mp4" "$(video_size "$OUT/review.mp4")"
    printf '提出用: %s\n' "$OUT/review.mp4"
  else
    printf '録画ファイルが無いか壊れています（%s）。QuickTime で録っていればそちらを。\n' "$OUT/review.mp4.log"
  fi
else
  printf 'QuickTime の収録を止めて保存してください。\n'
fi
printf '\n結果: %s/results.tsv\n' "$OUT"
cat "$OUT/results.tsv"
printf '\n提出前に動画を通しで見て、4 権限それぞれの場面と同意画面が映っていることを確かめてください。\n'
