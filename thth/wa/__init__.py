"""WhatsApp の口（`thth wa …`・計画 Meta 全部盛り §1.13・1.14）。

WhatsApp は Facebook のページの鍵では扱えない。WABA（WhatsApp Business Account）と、ユーザーの鍵（かシステム
ユーザーの鍵）が要る。だから広告（`thth/ads/`）と同じ形にする: 専用の台帳 `waba/<名前>.json`・専用の認可
（`thth wa auth`）・専用の命令の群。

- `ledger.py`: 台帳 `waba/<名前>.json` と状態 `waba_state/<名前>/`（止めの印・ロック・認可の途中・runs）。
- `auth.py`: `thth wa auth`（Facebook ログイン for Business と同じ流れで、ユーザーの長い鍵を取る）。
- `graph.py`: Graph への口。GET は `_get()`、POST は `_post()` の各 1 か所だけ。
- `cli.py`: 命令。書く口（template create・send）は二段確認・production・止めの印・結果不明の扱い。

本物の Meta・VM には出ない作り（試験は偽の Graph）。
"""
