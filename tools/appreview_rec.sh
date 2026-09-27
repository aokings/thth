#!/bin/bash
# App Review の録画 0「審査員の流れ」（2026-09-27・3.14.2 の形）。台本は
# docs/手順_Meta申請_世間の層_2026-09-24.md §2.1（審査員の手順）と §5 の録画 0。
#
#   bash tools/appreview_rec.sh [project]      # 既定 jiangshi-lab
#   REC_RESUME=<前回の保存先> REC_ACCOUNT=<口座> bash tools/appreview_rec.sh
#       … 区間 1（招待→認可）と secret・鍵の控えを済ませた続き（区間 2）から撮る
#   REC_RESUME=<保存先> REC_ACCOUNT=<口座> REC_KEEP=2 bash tools/appreview_rec.sh
#       … 区間 1・2（login まで）を残し、区間 3（命令）から撮り直す（REC_KEEP=3 なら区間 4 だけ）
#
# 招待リンク → Threads の認可 → 完了ページ →（録画の外で secret と鍵を控える）→ pip install
# → thth login（ブラウザで許可）→ posts → send → アプリで確認 → send --reply-to → replies
# → collect・measured → retract → アプリから消える → posts → /activity、を 1 本に撮る。
# 承認ページは無い（3.13.0）。運営者の命令は映さない（招待の作成と最短間隔 0 は録画の前）。
#
# 人の手（ブラウザとスマホ）は Return で進め、端末の命令と録画の開け閉めはこのスクリプトがやる。
# 端末の命令は審査員と同じ立場で **手元の thth（pip）** を打つ（画面には `$ thth …` と出す）。
#
# 秘密を映さない作り（台本 §5.0）:
#   - 招待 URL は録画の前に tty に出す。ブラウザへ移したら画面と scrollback を消してから録り始める。
#     アドレス欄は Chrome の app 窓で出さない。
#   - 口座の secret と鍵は録画を止めている間に完了ページで見て控える（区間を分けて撮る）。
#   - thth login のブラウザの画面（/login/…）は鍵が出ないので映してよい。
#   - 投稿が出る・消えるまでの待ちも録画を止めて待つ。
#
# 録画は ffmpeg avfoundation（止めるのは標準入力の q）。前提: Terminal.app・/opt/homebrew/bin/ffmpeg・
# 画面収録の許可・ssh wt（運営者の命令用）・pip の thth（3.14.2 以上）。
set -u
umask 077

PROJECT="${1:-jiangshi-lab}"
BY="${REC_BY:-masaru}"
VM="${REC_VM:-wt}"
LOCAL_THTH="${REC_LOCAL_THTH:-python3 -m thth}"   # 手元の thth（ラッパ ~/.local/bin/thth は VM 行きなので使わない）
FFMPEG="${FFMPEG_BIN:-/opt/homebrew/bin/ffmpeg}"
RESUME="${REC_RESUME:-}"
OUT="${RESUME:-${REC_OUT:-$HOME/Movies/thth-appreview-rec-$(date +%Y%m%d-%H%M%S)}}"
TOP=25; RW=2560; RH=1080          # 録る範囲（point）: x 0〜2560・y 25〜1105
CHROME_W=1200; TERM_X=1600        # Chrome 0〜1200・iPhone ミラーリング 1200〜1600・Terminal 1600〜2560
BODY='Testing THTH for Meta App Review: published from the command line by the account owner.'
REPLY='A reply, sent the same way.'
mkdir -p "$OUT" || exit 2

# ---------------------------------------------------------------- VM（運営者・録画の外だけ） ----
vm() {
  local q=''
  for a in "$@"; do q="$q $(printf '%q' "$a")"; done
  ssh "$VM" "PATH=\$HOME/.local/bin:\$PATH; export THTH_ROOT=/srv/thth; thth$q"
}

# ---------------------------------------------------------------- 手元（審査員と同じ立場） ----
local_json() { $LOCAL_THTH "$@" --json 2>/dev/null; }

show() {                         # 打つ命令を `$ thth …` と見せてから手元の thth で打つ
  printf '$ thth'
  for arg in "$@"; do
    case "$arg" in *[[:space:]]*) printf ' "%s"' "$arg" ;; *) printf ' %s' "$arg" ;; esac
  done
  printf '\n\n'
  sleep 1.5
  $LOCAL_THTH "$@" 2>&1
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

rec_start() {
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
latest_post() {                  # 手元の thth で直近の投稿の id（無ければ空）
  local_json posts "$1" --limit 1 | python3 -c '
import json, sys
try:
    posts = json.load(sys.stdin).get("posts") or []
    print(posts[0].get("id") or posts[0].get("post_id") or "" if posts else "")
except Exception:
    print("")'
}

# ---------------------------------------------------------------- 下ごしらえ（録画の外） ----
clear_all
cat <<EOF
THTH — App Review 録画 0（審査員の流れ・3.14.2）
project: $PROJECT   保存先: $OUT   手元の thth: $LOCAL_THTH

録画の前に:
  - 通知を切り（集中モード）、関係ない窓を閉じる
  - スマホの Threads を Mac に映す（iPhone ミラーリング）。撮る口座を開いておく
  - 録る範囲は画面の左上 2560×1080。Chrome は左（0〜1200）、Terminal は右（1600〜2560）に
    このスクリプトが置く。iPhone ミラーリングの窓はその間（1200〜1600・上寄り）に置く
  - それ以外の窓は、範囲の外（右端 2560 より右）へ寄せるか隠す
  - ブラウザ（Google Chrome）で、撮る口座で Threads にログインしておく
  - 手元の thth は login していない状態にする（このスクリプトが logout する）
EOF

[ -x "$FFMPEG" ] || { printf '\n%s がありません。\n' "$FFMPEG"; exit 2; }
SCREEN="$(screen_device)"
[ -n "$SCREEN" ] || { printf '\nffmpeg から画面が見えません（画面収録の許可 → ターミナル）。\n'; exit 2; }
$LOCAL_THTH --version >/dev/null 2>&1 || { printf '\n手元の thth（%s）が動きません。pip install -U thth\n' "$LOCAL_THTH"; exit 2; }

printf '\nVM を確かめます … '
if [ "$(ssh "$VM" 'systemctl is-active thth-worker' 2>/dev/null)" != active ]; then
  printf '常駐が動いていません（systemctl status thth-worker）。\n'; exit 2
fi
printf 'worker active\n'

/usr/bin/osascript >/dev/null 2>&1 <<APPLE || true
tell application id "com.apple.Terminal"
  set font size of selected tab of front window to 16
  set bounds of front window to {$TERM_X, $TOP, $RW, $((TOP + RH))}
end tell
APPLE

KEEP="${REC_KEEP:-1}"
if [ -n "$RESUME" ]; then
  ACCOUNT="${REC_ACCOUNT:?REC_ACCOUNT に口座名を}"
  head -n "$KEEP" "$OUT/segments.txt" > "$OUT/segments.keep" && mv "$OUT/segments.keep" "$OUT/segments.txt"
  SEG="$KEEP"
  printf '\n続きから撮ります（口座 %s・区間 1〜%s は残す）。\n' "$ACCOUNT" "$KEEP"
else
$LOCAL_THTH logout >/dev/null 2>&1 || true
wait_return '  [Return で招待を作る（URL はこの画面にだけ 1 回出ます・まだ録画しません）] '
printf '\n'
ssh -tt "$VM" "PATH=\$HOME/.local/bin:\$PATH; export THTH_ROOT=/srv/thth; thth admin invite create --media threads --project $(printf '%q' "$PROJECT") --label rec-0 --expires 1d --production --by $(printf '%q' "$BY")" || exit 2
ACCOUNT="$(vm admin invite list --json 2>/dev/null | python3 -c '
import json, sys
rows = [r for r in json.load(sys.stdin)["invites"] if r.get("label") == "rec-0" and r.get("status") in ("open", "registering", "unknown")]
rows.sort(key=lambda r: r.get("at") or "")
print(rows[-1]["account"] if rows else "")')"
[ -n "$ACCOUNT" ] || { printf '\n招待の口座名が取れませんでした（thth admin invite list）。\n'; exit 2; }
cat <<EOF

口座名: $ACCOUNT
上の招待 URL を選んでコピーし、Return を押してください。Chrome のアドレス欄の無い窓（左半分）で開きます。
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

# ---------------------------------------------------------------- 区間 1: 招待 → 認可 → 完了 ----
rec_start || exit 2
banner "1. The person opens the invitation link the operator created for them."
cat <<'EOF'
The page lists every permission THTH will request.
The person presses "Authorize with Threads", logs in with their own
Threads account and grants the permissions on Meta's screen.
"Your account is ready" then shows the THTH account name.
EOF
wait_return '  [招待のページ → Authorize with Threads → Threads の認可画面で権限の一覧を見せて許可 → "Your account is ready" が出たら Return] '
rec_stop

# ---------------------------------------------------------------- 録画の外: secret と鍵 ----
clear_all
cat <<EOF
（録画を止めています）
"Show account secret" を押し、口座の secret とアシスタントの鍵をパスワード管理に控えてください（口座 $ACCOUNT）。
控えたら、そのページを閉じるか "Your account is ready" に戻してください。
EOF
wait_return '  [控えて secret と鍵が画面から消えたら Return] '
printf '最短間隔を 0 にします（審査の間だけ・§2.2 の 3）… '
vm account set "$ACCOUNT" min_interval_hours 0 --by "$BY" >/dev/null 2>&1 && printf 'ok\n' || printf '（失敗・手で: thth account set %s min_interval_hours 0 --by %s）\n' "$ACCOUNT" "$BY"
wait_return '  [Return で録画を再開（区間 2: pip install と thth login）] '
fi

# ---------------------------------------------------------------- 区間 2: pip install → thth login ----
if [ "$KEEP" -lt 2 ]; then
rec_start || exit 2
banner "2. On their own computer, the person installs the command-line tool and signs in."
printf '$ pip install -U thth\n\n'; sleep 1
pip install -U thth 2>&1 | grep -vE "^WARNING: The directory|cache has been disabled|If executing pip with sudo" | tail -3
printf '\n'
printf '$ thth login\n\n'; sleep 1
$LOCAL_THTH login 2>&1 &
LOGIN_PID=$!
wait_return '  [左の Chrome で https://thth.me/login/… に口座名と secret → "Allow this device" → 端末に「保存しました」が出たら Return] '
wait "$LOGIN_PID" 2>/dev/null || true
rec_stop

clear_all
printf '（録画を止めて、サーバが鍵を切り替えるのを待っています …）\n'
for _ in $(seq 1 30); do
  $LOCAL_THTH account status "$ACCOUNT" >/dev/null 2>&1 && break
  sleep 5
done
$LOCAL_THTH account status "$ACCOUNT" >/dev/null 2>&1 || { printf '鍵がまだ通りません。thth account status %s を手で確かめてください。\n' "$ACCOUNT"; exit 2; }
printf '通りました。スマホの Threads を、投稿が見える画面（プロフィール）にしてください。\n'
wait_return '  [Return で録画を再開（区間 3: 命令を打つ）] '
fi

# ---------------------------------------------------------------- 区間 3: posts → send → reply → replies → measured → retract ----
if [ "$KEEP" -lt 3 ]; then
rec_start || exit 2
banner "3. threads_basic — the account's own posts."
show posts "$ACCOUNT" --limit 3
sleep 3
banner "4. threads_content_publish — publish a post from the command line."
BEFORE="$(latest_post "$ACCOUNT")"
SEND_OUT="$(show send "$ACCOUNT" --text "$BODY")"
printf '%s\n' "$SEND_OUT"
POST="$(printf '%s\n' "$SEND_OUT" | grep -oE 'post_id[^0-9]*[0-9]{10,}' | grep -oE '[0-9]{10,}' | head -1)"
if [ -z "$POST" ]; then
  for _ in $(seq 1 12); do POST="$(latest_post "$ACCOUNT")"; [ -n "$POST" ] && [ "$POST" != "$BEFORE" ] && break; POST=''; sleep 5; done
fi
[ -n "$POST" ] || { rec_stop; printf '\n投稿の id が取れませんでした。上の出力を見てください。\n'; exit 2; }
wait_return '  [スマホの Threads で投稿が見えたら（画面に映して）Return] '
banner "5. threads_manage_replies — a reply, sent the same way."
show send "$ACCOUNT" --text "$REPLY" --reply-to "$POST"
wait_return '  [スマホで投稿の下に返信が見えたら Return] '
banner "6. threads_read_replies — read the replies to the account's posts."
show replies "$ACCOUNT" --refresh --limit 3
sleep 3
banner "7. threads_manage_insights — fetch and show the post metrics."
show collect "$ACCOUNT"
show measured "$ACCOUNT" --limit 3
sleep 3
banner "8. threads_delete — delete the post, then confirm in the Threads app."
show retract "$ACCOUNT" "$POST" --reason "App Review recording"
wait_return '  [スマホで投稿が消えたのを見せたら Return] '
show posts "$ACCOUNT" --limit 3
sleep 3
rec_stop
fi

# ---------------------------------------------------------------- 区間 4: /activity ----
clear_all
printf '左の Chrome で https://thth.me/activity を開き、口座名と secret で入ってください（まだ録画していません）。\n'
wait_return '  [一覧が出たら Return で録画（10 秒ほど・「止める」のボタンまで見せる）] '
rec_start || exit 2
banner "9. The owner's activity page: what was published and deleted, and a stop button."
printf 'No approval step exists. The owner'"'"'s own commands are the only trigger;\nsafety limits (interval, daily caps, burst stop) are enforced by the server.\n'
wait_return '  [一覧をスクロールして「止める」まで見せたら Return で終了] '
rec_stop

# ---------------------------------------------------------------- つなぐ ----
clear_all
LIST="$OUT/concat.txt"; : > "$LIST"
while read -r f; do printf "file '%s'\n" "$f" >> "$LIST"; done < "$OUT/segments.txt"
"$FFMPEG" -hide_banner -y -f concat -safe 0 -i "$LIST" -c copy "$OUT/review0.mp4" >/dev/null 2>&1 \
  && printf 'できました: %s\n' "$OUT/review0.mp4" \
  || printf 'つなげませんでした（区間は %s に残っています）。\n' "$OUT"
cat <<EOF

片付け（§5.3）:
  - 口座 $ACCOUNT を外す: thth account leave $ACCOUNT --by $BY   （VM・録画に使った口座）
  - 手元の鍵を消す: thth logout
  - 映った秘密が無いか、review0.mp4 を一度見る（招待 URL・secret・鍵）
EOF
