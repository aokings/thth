#!/usr/bin/env bash
# X の開発者アプリの client（OAuth 2.0・confidential）を、運営のサーバーの THTH に入れる。
# Client ID と Client Secret を聞くので貼る（画面に出ない・引数・履歴・ファイルに残らない）。
# 使い方（ターミナル.app で。Claude の Run ボタンでは使わない）: bash tools/x_app_set.sh
set -euo pipefail
HOST="${THTH_SSH_HOST:-wt}"
BY="${THTH_BY:-masaru}"
read -r -s -p "X の Client ID を貼って Enter: " client_id; echo
read -r -s -p "X の Client Secret を貼って Enter: " client_secret; echo
[ -n "$client_id" ] && [ -n "$client_secret" ] || { echo "空でした。もう一度やり直してください。" >&2; exit 2; }
printf '%s\n%s\n' "$client_id" "$client_secret" \
  | python3 -c 'import json,sys; i,s=sys.stdin.read().splitlines()[:2]; print(json.dumps({"client_id":i,"client_secret":s,"client_type":"confidential","redirect_uri":"https://thth.me/callback/"}))' \
  | ssh "$HOST" "PATH=\"\$HOME/.local/bin:\$PATH\"; exec thth app set x --by $(printf '%q' "$BY") --stdin"
unset client_id client_secret
echo "入れました。確かめ: ssh $HOST 'PATH=\$HOME/.local/bin:\$PATH thth app show --json'"
