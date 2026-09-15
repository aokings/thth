#!/bin/bash
# 自動録画の切り分け（2026-09-15）。appreview_demo.sh の録画の作りを、この Mac で 3 秒ずつ試す。
#
#   bash tools/appreview_rectest.sh
#
# 1 回目の結果（2026-09-15・masaru の Mac mini・3840×1600 等倍）:
#   A 前面 screencapture -V3         → OK 1920x1080
#   B 背景 screencapture -V3（INT なし）→ OK 1920x1080
#   C 背景 screencapture、3 秒後に INT → **ファイルを書かずに死ぬ**（INT は停止でなく中断）
# よって screencapture は「時間を決めて回す」しかできず、途中で止められない。
# 2 回目はここから: ffmpeg（avfoundation）で録れるか、INT できれいに止まるか。
set -u
REGION="${DEMO_REGION:-0,25,1920,1080}"
OUT="${DEMO_OUT:-$HOME/Movies/thth-rectest-$(date +%H%M%S)}"
mkdir -p "$OUT"
FFMPEG=/opt/homebrew/bin/ffmpeg
FFPROBE=/opt/homebrew/bin/ffprobe
IFS=, read -r RX RY RW RH <<<"$REGION"

report() {   # $1 = 名前  $2 = ファイル
  if [ -s "$2" ]; then
    size="$($FFPROBE -v error -select_streams v:0 -show_entries stream=width,height -show_entries format=duration -of csv=p=0 "$2" 2>/dev/null | tr '\n' ' ')"
    printf '  %s: OK  %s bytes  %s\n' "$1" "$(stat -f %z "$2")" "${size:-（ffprobe 読めず）}"
  else
    printf '  %s: 失敗（ファイル無し・空）  log 末尾: %s\n' "$1" "$(tail -3 "$2.log" 2>/dev/null | tr '\n' ' ')"
  fi
}

if pgrep -x ffmpeg >/dev/null; then
  printf '前の ffmpeg がまだ動いています。先に  pkill -INT ffmpeg  で止めて、30 秒待ってから再実行してください。\n'
  exit 1
fi
printf '範囲 %s / 保存先 %s\n\n' "$REGION" "$OUT"

printf 'D: ffmpeg が見える画面の一覧…\n'
$FFMPEG -hide_banner -f avfoundation -list_devices true -i "" 2>&1 | grep -iE "screen|AVFoundation video" | sed 's/^/  /'
SCREEN="$($FFMPEG -hide_banner -f avfoundation -list_devices true -i "" 2>&1 | grep -iE "\] \[[0-9]+\] Capture screen" | head -1 | sed -E 's/.*\[([0-9]+)\] Capture screen.*/\1/')"
if [ -z "$SCREEN" ]; then printf '  画面の device が見つかりません。ここで終わります。\n'; exit 1; fi
printf '  使う device: %s\n' "$SCREEN"

# 共通の録り方: 画面全体を取り込み、範囲を切り抜き、ハードウェア H.264、30fps、音なし。
# **`exec` で自分自身を ffmpeg にする**——`rec … &` で背景に回したとき、`$!` が ffmpeg
# の pid になるように（2 回目の試験は外側の bash に信号を送っていて、ffmpeg は孤児に
# なって回り続けた）。perl は `&` の巻き添えで無視になった INT を既定に戻してから exec。
rec() {   # $1 = 出力  残り = 追加オプション（-t 3 など）
  out="$1"; shift
  exec /usr/bin/perl -e '$SIG{INT}="DEFAULT"; exec @ARGV or die "exec: $!"' \
    $FFMPEG -hide_banner -y -f avfoundation -framerate 30 -capture_cursor 1 -i "$SCREEN:none" \
    -vf "crop=$RW:$RH:$RX:$RY" -c:v h264_videotoolbox -b:v 8M -pix_fmt yuv420p -an "$@" "$out"
}

printf 'E: ffmpeg 前面で 3 秒（-t 3）…\n'
( rec "$OUT/E.mp4" -t 3 </dev/null 2>"$OUT/E.mp4.log" )
report E "$OUT/E.mp4"

printf 'F: ffmpeg 背景で開始 → 4 秒後に標準入力へ q（ffmpeg 自身の終了の合図）…\n'
# 3 回目の知見: `&` で生まれた ffmpeg は INT も TERM も無視した（孤児が 5 分回り続けた）。
# 信号に頼らず、ffmpeg が自分で見ている標準入力に q を書く。名前付きパイプを読み書きで
# 開いておけば（exec 3<>）、開く側が待たされず、書いた q がそのまま ffmpeg に届く。
CTL="$OUT/ctl.fifo"; rm -f "$CTL"; mkfifo "$CTL"
exec 3<>"$CTL"
rec "$OUT/F.mp4" <&3 2>"$OUT/F.mp4.log" &
pid=$!
sleep 4
printf '  背景の pid %s は: %s\n' "$pid" "$(ps -o command= -p "$pid" | cut -c1-60)"
printf 'q' >&3
for i in $(seq 1 300); do kill -0 "$pid" 2>/dev/null || break; sleep 0.1; done
if kill -0 "$pid" 2>/dev/null; then
  printf '  F: q を送って 30 秒たっても生きている（stat=%s）→ KILL します\n' "$(ps -o stat= -p "$pid")"
  kill -KILL "$pid" 2>/dev/null
fi
wait "$pid" 2>/dev/null
exec 3>&-
printf '  ffmpeg の最後の言葉: %s\n' "$(tail -c 400 "$OUT/F.mp4.log" | tr '\r' '\n' | grep -vE '^frame=|^\s*$' | tail -1)"
report F "$OUT/F.mp4"

printf '\nこの画面をそのまま貼ってください。E と F が両方 OK なら、この録り方で本番を組みます。\n'
