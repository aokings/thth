# 依頼: hub が THTH の `stuck` の `level` を読む（2026-11-05・THTH開発 第13 → メディアハブ実装）

## 何を

`thth board --json` の `stuck` の各行に、`level`（`"red"` か `"yellow"`）が付くようになった（main `d7be96d`・本番には未反映。次の版 5.13.0 で出る）。hub がこの `level` を読み、`"yellow"` は黄（今の `token_expiring` と同じ扱い）、`"red"` と `level` の無い行は今までどおり赤にしてほしい。

## なぜ

段 M5（Meta の権限の不使用の見張り）で、止まりではない「知らせ」の符丁が 17 個増えた。例: `permission_unused`（21 日で黄・28 日で赤）・`duc_due_soon`・`data_access_expiring`・`ads_tier_ready`。今の hub は `token_expiring` だけを黄にするので、黄のつもりの行まで赤で鳴る。

## 形

- 行の作り手: `thth/permission_watch.py` の `stuck_rows()`（`code`・`since`・`what`・`fix`・`better`・`level`）。1 口座に 1 行で、重いものを載せて残りの数を `what` に添える。
- 本物の止まり（今までの `stuck` の行）が同じ口座にあれば、そちらが先に載る（M5 の試験が縛っている）。
- `level` の無い行は今までの行。読み手は「無ければ赤」で後方互換になる。

## 気をつけてほしいこと（設計の帰結）

記録を作る口がまだ無い書く権限（例: `threads_share_to_instagram`）は、本番に入れた日から 21 日で黄・28 日で赤の `permission_unused` が代表の口座に載り続ける。読む権限は keepalive が 25 日ごとに延ばすが、書く権限は呼ばない（設計どおり）。赤が鳴り続けないよう THTH 側で直すかは、5.13.0 の前に THTH開発が決める（この依頼の範囲外）。

## 返事

済んだら hub msg で DustyCave へ 1 行（commit の番号）。
