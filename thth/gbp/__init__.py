"""Google ビジネス プロフィールの口（持ち主か管理者の店だけ・書く口は二段確認）。

- `auth.py`: 利用者の Google アカウントの OAuth（`thth/google_oauth.py` を `thth gads`・`thth gsc` と共通で使う・
  scope は `business.manage`・鍵は gads・gsc と別のファイル）。
- `api.py`: REST（urllib だけ）・Google の断りを THTH の符丁へ・臨時の時間の組み立て。
- `cli.py`: `thth gbp auth|doctor|locations|show|special|posts|post`。

Google の決まり（設計 実店舗 §6.4・§9.4・https://developers.google.com/my-business/content/policies）に合わせて:
読んだ中身はファイルに残さない（性能のための一時の保存は 30 日までの決まり。この口は画面に出すだけで、何も貯めない）・
書き換え（臨時の時間・投稿）は持ち主の二段確認のあとだけ（自動にしない）・Google との提携を匂わせる語を出さない。
MCP と遠くの道（運営者のサーバー）には出さない。
"""
