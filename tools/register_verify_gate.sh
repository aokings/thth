#!/bin/sh
# 台帳の器 段 3: 公開の直前に、封と公開鍵で確かめる台本（CI でも、CI を持たないメディアの手元の配信でも使う）。
# 設計 docs/設計_台帳の器_2026-11-13.md §5.6「CI と承認」・§12（前に配った世代をどこから読むか）。
#
#   tools/register_verify_gate.sh <台帳> <出し口> <材料の先> <前の封の URL | first> [<共通の台帳>]
#
# - 公開鍵は CI の変数 THTH_REGISTER_SEAL_PUBLIC_KEY だけ（repo の木の外。repo の中の鍵のファイルは読まない）。
#   置くのは持ち主の手か承認で（`thth register key show` の base64 の 1 行）。
# - THTH は、版を止めて入れた物を使う（THTH_PIN に版を書く。例 `pip install thth==$THTH_PIN` を前の段で）。
#   配ろうとしている変更の中の処理は使わない。版が違えば止まる。
# - 前に配った世代の封は、公開の場所に材料と一緒に配った `thth-seal.<出し口>.json` を、この台本が取ってくる
#   （THTH は網に出ないので、取るのは台本の役目）。初めて配るときだけ `first`。取れなければ止まる。
# - 確かめが緑なら、今の封を `<材料の先>/thth-seal.<出し口>.json` に置く（次の回が「前に配った世代」として読む）。
#
# 正直な残り（設計 §5.6）: main に書ける者がこの台本や workflow を書き換える・履歴ごと書き換える、は防げない。
set -eu

if [ "$#" -lt 4 ]; then
  echo "使い方: $0 <台帳> <出し口> <材料の先> <前の封の URL | first> [<共通の台帳>]" >&2
  exit 2
fi
ledger=$1
outlet=$2
material=$3
previous=$4
common=${5:-}
THTH=${THTH:-thth}

if [ -z "${THTH_REGISTER_SEAL_PUBLIC_KEY:-}" ]; then
  echo "公開鍵がありません: CI の変数 THTH_REGISTER_SEAL_PUBLIC_KEY に持ち主の公開鍵を置いてください（thth register key show）" >&2
  exit 1
fi
if [ -n "${THTH_PIN:-}" ]; then
  have=$($THTH --version | awk '{print $2}')
  if [ "$have" != "$THTH_PIN" ]; then
    echo "THTH の版が止めた版と違います（止めた版 ${THTH_PIN}・入っている版 ${have}）" >&2
    exit 1
  fi
fi

work=$(mktemp -d "${TMPDIR:-/tmp}/thth-gate.XXXXXX")
set -- register verify "$ledger" --outlet "$outlet" --material "$material"
if [ "$previous" = "first" ]; then
  set -- "$@" --no-previous
else
  if ! curl -fsSL --max-time 30 "$previous" -o "$work/previous.json"; then
    echo "前に配った世代の封を取れません（${previous}）。取れないまま配ると、古い封への巻き戻しを見落とします" >&2
    exit 1
  fi
  set -- "$@" --previous "$work/previous.json"
fi
if [ -n "$common" ]; then
  set -- "$@" --common "$common"
fi
$THTH "$@"
cp "$ledger/.thth/seals/$outlet.json" "$material/thth-seal.$outlet.json"
echo "封を材料の先に置きました: ${material}/thth-seal.${outlet}.json（配ると、次の回の「前に配った世代」になる）"
