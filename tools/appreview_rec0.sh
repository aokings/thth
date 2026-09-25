#!/bin/bash
# App Review の録画 0「審査員の流れ」（2026-09-25）。台本は docs/手順_Meta申請_世間の層_2026-09-24.md §5 の録画 0。
#
#   bash tools/appreview_rec0.sh [project]      # 既定 jiangshi-lab
#   REC_RESUME=<前回の保存先> REC_ACCOUNT=<口座> bash tools/appreview_rec0.sh
#       … 区間 1（招待→認可）と secret を済ませた続きの区間 2 から撮る
#
# 招待リンク → Threads の認可 → 下書きと承認の依頼 → 承認ページで承認 → アプリに投稿
# → 削除の依頼 → 承認 → アプリから消える、を 1 本に撮る。人の手（ブラウザとスマホ）は
# Return で進め、端末の命令と録画の開け閉めはこのスクリプトがやる。
#
# 秘密を映さない作り（台本 §5.0）:
#   - 招待 URL は録画の前に tty に出す（VM の thth が tty にだけ出す）。ブラウザへ移したら
#     画面と scrollback を消してから録り始める。アドレス欄は Chrome の app 窓で出さない。
#   - 承認 secret は録画を止めている間に見て控える。区間を分けて撮り、最後につなぐ。
#   - 承認 URL は端末の表示で伏せる（一覧 https://thth.me/pending から開くので要らない）。
#   - 投稿が出る・消えるまでの待ちも録画を止めて待つ（worker の巡回しだいで数十秒〜数分）。
#
# 録画の作りは tools/appreview_demo.sh と同じ（ffmpeg avfoundation・止めるのは標準入力の q）。
# 前提: Terminal.app・/opt/homebrew/bin/ffmpeg・画面収録の許可・ssh wt。
set -u
umask 077

PROJECT="${1:-jiangshi-lab}"
BY="${REC_BY:-masaru}"
VM="${REC_VM:-wt}"
FFMPEG="${FFMPEG_BIN:-/opt/homebrew/bin/ffmpeg}"
FFPROBE="${FFPROBE_BIN:-/opt/homebrew/bin/ffprobe}"
RESUME="${REC_RESUME:-}"
OUT="${RESUME:-${REC_OUT:-$HOME/Movies/thth-appreview-rec0-$(date +%Y%m%d-%H%M%S)}}"
TOP=25; RW=2560; RH=1080          # 録る範囲（point）: x 0〜2560・y 25〜1105
CHROME_W=1200; TERM_X=1600        # Chrome 0〜1200・iPhone ミラーリング 1200〜1600・Terminal 1600〜2560
BODY='THTH test post: approved on a thth.me approval page before publishing. / 承認してから出す投稿の試しです。'
mkdir -p "$OUT" || exit 2

# ---------------------------------------------------------------- VM ----
vm() {                           # VM の thth を打つ（引数は 1 つずつ quote して渡す）
  local q=''
  for a in "$@"; do q="$q $(printf '%q' "$a")"; done
  ssh "$VM" "PATH=\$HOME/.local/bin:\$PATH; export THTH_ROOT=/srv/thth; thth$q"
}

mask() {                         # 承認 URL を伏せる（一覧の URL は伏せない）
  sed -E 's#(https://thth\.me/approve/)[^ ]*#\1••••••••#g'
}

latest_post() {                  # $1 = 口座 → 直近の投稿の id（無ければ空）
  vm posts "$1" --limit 1 --json 2>/dev/null | python3 -c '
import json, sys
try:
    posts = json.load(sys.stdin).get("posts") or []
    print(posts[0]["id"] if posts else "")
except Exception:
    print("")'
}

# ---------------------------------------------------------------- 録画 ----
SEG=0
REC_PID=''
CTL="$OUT/ctl.fifo"
SCREEN=''

screen_device() {
  "$FFMPEG" -hide_banner -f avfoundation -list_devices true -i "" 2>&1 \
    | grep -iE "\] \[[0-9]+\] Capture screen" | head -1 \
    | sed -E 's/.*\[([0-9]+)\] Capture screen.*/\1/'
}

ffmpeg_run() {                   # 左上の 2560×1080（point）を等倍で撮る。Retina なら REC_SCALE=2
  out="$1"; shift
  s="${REC_SCALE:-1}"
  exec "$FFMPEG" -hide_banner -y -f avfoundation -framerate 30 -capture_cursor 1 \
    -i "$SCREEN:none" -vf "crop=$((RW * s)):$((RH * s)):0:$((TOP * s)),scale=$RW:$RH" -c:v h264_videotoolbox -b:v 8M \
    -pix_fmt yuv420p -an "$@" "$out"
}

rec_start() {                    # 次の区間を撮り始める
  SEG=$((SEG + 1))
  seg="$OUT/seg$SEG.mp4"
  rm -f "$CTL"; mkfifo "$CTL" || return 1
  exec 3<>"$CTL"
  ffmpeg_run "$seg" <&3 2>"$seg.log" &
  REC_PID=$!
  sleep 2
  if ! kill -0 "$REC_PID" 2>/dev/null; then
    wait "$REC_PID" 2>/dev/null; REC_PID=''; exec 3>&-
    printf '\n**録画が始まりませんでした**（%s）\n' "$seg.log"
    return 1
  fi
  printf '%s\n' "$seg" >> "$OUT/segments.txt"
}

rec_stop() {
  [ -n "$REC_PID" ] || return 0
  printf 'q' >&3 2>/dev/null || true
  for _ in $(seq 1 300); do
    if ! kill -0 "$REC_PID" 2>/dev/null; then
      wait "$REC_PID" 2>/dev/null || true; REC_PID=''; exec 3>&-; return 0
    fi
    sleep 0.1
  done
  kill -KILL "$REC_PID" 2>/dev/null || true
  wait "$REC_PID" 2>/dev/null || true; REC_PID=''; exec 3>&-
  return 1
}

trap 'rec_stop; exit 130' INT TERM
trap 'rec_stop' EXIT

# ---------------------------------------------------------------- 進行 ----
wait_return() { printf '\n'; read -r -p "$1" _ </dev/tty; }

clear_all() { printf '\033[3J\033[H\033[2J'; }

banner() {                       # 審査員に見せる見出し（英語）
  clear_all
  printf 'THTH — Threads App Review: reviewer flow (video 0)\n'
  printf '%s\n' "$1"
  printf '%s\n\n' "----------------------------------------------------------------"
}

show() {                         # 打つ命令を見せてから VM で打つ（出力は承認 URL を伏せる）
  printf '$ thth'
  for arg in "$@"; do
    case "$arg" in *[[:space:]]*) printf ' "%s"' "$arg" ;; *) printf ' %s' "$arg" ;; esac
  done
  printf '\n\n'
  sleep 1.5
  vm "$@" 2>&1 | mask
  return "${PIPESTATUS[0]}"
}

# ---------------------------------------------------------------- 下ごしらえ（録画の外） ----
clear_all
cat <<EOF
THTH — App Review 録画 0（審査員の流れ）
project: $PROJECT   保存先: $OUT

録画の前に:
  - 通知を切り（集中モード）、関係ない窓を閉じる
  - スマホの Threads を Mac に映す（iPhone ミラーリング）。撮る口座（jiangshi_lab）を開いておく
  - 録る範囲は画面の左上 2560×1080。Chrome は左（0〜1200）、Terminal は右（1600〜2560）に
    このスクリプトが置く。iPhone ミラーリングの窓はその間（左端から 1200〜1600・上寄り）に置く
  - それ以外の窓（ふだんの Chrome など）は、範囲の外（画面の右端 2560 より右）へ寄せるか隠す
  - ブラウザ（Google Chrome）で、撮る口座で Threads にログインしておく
EOF

[ -x "$FFMPEG" ] || { printf '\n%s がありません。\n' "$FFMPEG"; exit 2; }
SCREEN="$(screen_device)"
[ -n "$SCREEN" ] || { printf '\nffmpeg から画面が見えません（画面収録の許可 → ターミナル）。\n'; exit 2; }

printf '\nVM を確かめます … '
if [ "$(ssh "$VM" 'systemctl is-active thth-approval-worker' 2>/dev/null)" != active ]; then
  printf '承認 worker が動いていません（systemctl status thth-approval-worker）。\n'; exit 2
fi
printf '%s\n' "$BODY" | ssh "$VM" 'mkdir -p ~/rec && cat > ~/rec/post0.txt' || exit 2
printf 'worker active・本文を ~/rec/post0.txt に置いた\n'

# 録る範囲は画面の左上 2560×1080（menu bar の下から）。左から Chrome・iPhone ミラーリング・Terminal。
/usr/bin/osascript >/dev/null 2>&1 <<APPLE || true
tell application id "com.apple.Terminal"
  set font size of selected tab of front window to 16
  set bounds of front window to {$TERM_X, $TOP, $RW, $((TOP + RH))}
end tell
APPLE

if [ -n "$RESUME" ]; then
  ACCOUNT="${REC_ACCOUNT:?REC_ACCOUNT に口座名を}"
  # 区間 1 だけ残して続きから（失敗した区間 2 以降は捨てる）
  head -1 "$OUT/segments.txt" > "$OUT/segments.keep" && mv "$OUT/segments.keep" "$OUT/segments.txt"
  SEG=1
  printf '\n続きから撮ります（口座 %s・区間 1 は %s）。\n' "$ACCOUNT" "$(head -1 "$OUT/segments.txt")"
  printf 'Chrome の左の窓は "Your account is ready" の画面のままにしておいてください。\n'
else
wait_return '  [Return で招待を作る（URL はこの画面にだけ 1 回出ます・まだ録画しません）] '
printf '\n'
ssh -tt "$VM" "PATH=\$HOME/.local/bin:\$PATH; export THTH_ROOT=/srv/thth; thth admin invite create --media threads --project $(printf '%q' "$PROJECT") --label rec-0 --expires 1d --production --approval all --by $(printf '%q' "$BY")" || exit 2
ACCOUNT="$(vm admin invite list --json 2>/dev/null | python3 -c '
import json, sys
rows = [r for r in json.load(sys.stdin)["invites"] if r.get("label") == "rec-0" and r.get("status") in ("open", "registering", "unknown")]
rows.sort(key=lambda r: r.get("at") or "")
print(rows[-1]["account"] if rows else "")')"
[ -n "$ACCOUNT" ] || { printf '\n招待の口座名が取れませんでした（thth admin invite list）。\n'; exit 2; }
cat <<EOF

口座名: $ACCOUNT
上の招待 URL を選んでコピーし、Return を押してください。
Chrome のアドレス欄の無い窓（左半分）で開きます。
EOF
wait_return '  [URL をコピーしたら Return] '
INVITE_URL="$(pbpaste)"
case "$INVITE_URL" in
  https://thth.me/invite/*) ;;
  *) printf 'クリップボードが招待 URL ではありません。やり直してください。\n'; exit 2 ;;
esac
open -na "Google Chrome" --args --app="$INVITE_URL" --window-position=0,$TOP --window-size=$CHROME_W,$RH
INVITE_URL=''; printf '' | pbcopy
clear_all
wait_return '  [招待のページが左に出たら Return で録画を始める] '

# ---------------------------------------------------------------- 区間 1: 招待 → 認可 ----
rec_start || exit 2
banner "1. The person opens the invitation link the operator created for them."
cat <<'EOF'
The page lists every permission THTH will request.
The person presses "Authorize with Threads", logs in with their own
Threads account and grants the permissions on Meta's screen.
EOF
wait_return '  [招待のページ → Authorize with Threads → Threads の認可画面で権限の一覧を見せて許可 → "Your account is ready" が出たら Return] '
rec_stop

# ---------------------------------------------------------------- 録画の外: secret ----
clear_all
cat <<EOF
（録画を止めています）
"Show approval secret" を押し、secret をパスワード管理に控えてください（口座 $ACCOUNT）。
控えたら、ブラウザの secret のページを閉じるか、"Your account is ready" の画面に戻してください。
EOF
wait_return '  [控えて secret が画面から消えたら Return で録画を再開] '

fi

# ---------------------------------------------------------------- 区間 2: 下書き → 承認 ----
rec_start || exit 2
banner "2. The operator places a draft and requests approval."
printf 'THTH cannot publish it until the owner approves.\n\n'
DRAFT_OUT="$(vm admin draft put "$ACCOUNT" --body-file rec/post0.txt --by "$BY" 2>&1)"
printf '$ thth admin draft put %s --body-file ~/rec/post0.txt --by %s\n\n%s\n\n' "$ACCOUNT" "$BY" "$DRAFT_OUT"
DRAFT_ID="$(printf '%s\n' "$DRAFT_OUT" | sed -nE 's/.*draft_id=([^ ]+).*/\1/p' | head -1)"
if [ -z "$DRAFT_ID" ]; then
  rec_stop; printf '\n下書きが置けませんでした。上の出力を見てください。\n'; exit 2
fi
BEFORE="$(latest_post "$ACCOUNT")"
sleep 1
show admin approval request "$ACCOUNT" --draft "$DRAFT_ID" --send --by "$BY"
wait_return '  [ブラウザで https://thth.me/pending → ユーザ名と secret → Open → 本文を見せて secret → Approve → "Publication approval received" まで。済んだら Return] '
rec_stop

clear_all
printf '（録画を止めて、投稿が出るのを待っています …）\n'
POST=''
for _ in $(seq 1 60); do
  POST="$(latest_post "$ACCOUNT")"
  [ -n "$POST" ] && [ "$POST" != "$BEFORE" ] && break
  POST=''; sleep 10
done
[ -n "$POST" ] || { printf '10 分待っても投稿が出ません。thth approvals / worker のログを見てください。\n'; exit 2; }
printf '出ました: %s\nスマホの Threads で投稿が見える画面にしてください。\n' "$POST"
wait_return '  [投稿が見えたら Return で録画を再開] '

# ---------------------------------------------------------------- 区間 3: 公開の確認 → 削除の依頼 ----
rec_start || exit 2
banner "3. Only now is the post published on the owner's Threads account."
show posts "$ACCOUNT" --limit 1
sleep 4
banner "4. Deleting a post also needs the owner's approval."
show admin approval request "$ACCOUNT" --retract "$POST" --reason recording --by "$BY"
wait_return '  [ブラウザで一覧を読み込み直す → Open → "Delete this post" を見せて secret → Approve → "Deletion approval received" まで。済んだら Return] '
rec_stop

clear_all
printf '（録画を止めて、投稿が消えるのを待っています …）\n'
GONE=''
for _ in $(seq 1 60); do
  now="$(latest_post "$ACCOUNT")"
  [ "$now" != "$POST" ] && { GONE=1; break; }
  sleep 10
done
[ -n "$GONE" ] || { printf '10 分待っても消えません。thth approvals / worker のログを見てください。\n'; exit 2; }
printf '消えました。スマホの Threads で、投稿が無くなった画面（プロフィール）にしてください。\n'
wait_return '  [消えたのが見えたら Return で録画を再開] '

# ---------------------------------------------------------------- 区間 4: 削除の確認 ----
rec_start || exit 2
banner "5. After approval, THTH deletes the post, and it is gone from Threads."
show posts "$ACCOUNT" --limit 1
sleep 6
rec_stop

# ---------------------------------------------------------------- 仕上げ ----
clear_all
LIST="$OUT/concat.txt"; : > "$LIST"
while read -r s; do [ -s "$s" ] && printf "file '%s'\n" "$s" >> "$LIST"; done < "$OUT/segments.txt"
if "$FFMPEG" -hide_banner -loglevel error -y -f concat -safe 0 -i "$LIST" -c copy "$OUT/review0.mp4"; then
  dim="$("$FFPROBE" -v error -select_streams v:0 -show_entries stream=width,height \
    -show_entries format=duration -of csv=p=0 "$OUT/review0.mp4" 2>/dev/null | tr '\n' ' ')"
  printf '録画 0: %s（%s）\n' "$OUT/review0.mp4" "$dim"
else
  printf 'つなげませんでした。区間ごとの動画は %s/seg*.mp4 にあります。\n' "$OUT"
fi
cat <<EOF

提出前に通しで見て、secret・招待 URL・承認 URL が映っていないことを確かめてください。
字幕は台本（§5 録画 0）の英語の文を、場面の頭の見出しと合わせて付けます。
撮り直すなら: thth account leave $ACCOUNT --by $BY（と Threads の設定で THTH の接続を外す）
EOF
