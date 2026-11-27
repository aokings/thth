// 生成物: `python -m thth.mcp_remote_tools --write` が括りの通り道の表（thth/server_passthrough.py）から書き出す。
// 手で直さない。ずれは tests/test_mcp_remote_tools.py が赤にする。
export default {
 "operation": "ws_command",
 "schema": "thth.mcp-tools/1",
 "tools": [
  {
   "annotations": {
    "destructiveHint": false,
    "openWorldHint": false,
    "readOnlyHint": true
   },
   "argv": {
    "always": [
     "--json"
    ],
    "flags": {
     "compare_previous": "--compare-previous",
     "per_post_clicks": "--per-post-clicks",
     "weekly_goals": "--weekly-goals"
    },
    "positional": null,
    "texts": {},
    "values": {
     "by": "--by",
     "min_n": "--min-n",
     "project": "--project",
     "since": "--since",
     "weeks": "--weeks",
     "window_days": "--window-days"
    }
   },
   "command": "analytics-report",
   "confirm": null,
   "description": "数字の報告を読む（project・期間・切り口を選ぶ）。読むだけ",
   "inputSchema": {
    "additionalProperties": false,
    "properties": {
     "by": {
      "enum": [
       "attachment_kind",
       "goal",
       "hour_band",
       "kind",
       "tag",
       "topic"
      ],
      "type": "string"
     },
     "compare_previous": {
      "type": "boolean"
     },
     "min_n": {
      "maxLength": 64,
      "pattern": "^[A-Za-z0-9_.:+,-]{1,64}$",
      "type": "string"
     },
     "per_post_clicks": {
      "type": "boolean"
     },
     "project": {
      "maxLength": 64,
      "pattern": "^[A-Za-z0-9_.:+,-]{1,64}$",
      "type": "string"
     },
     "since": {
      "maxLength": 64,
      "pattern": "^[A-Za-z0-9_.:+,-]{1,64}$",
      "type": "string"
     },
     "weekly_goals": {
      "type": "boolean"
     },
     "weeks": {
      "maxLength": 64,
      "pattern": "^[A-Za-z0-9_.:+,-]{1,64}$",
      "type": "string"
     },
     "window_days": {
      "maxLength": 64,
      "pattern": "^[A-Za-z0-9_.:+,-]{1,64}$",
      "type": "string"
     }
    },
    "type": "object"
   },
   "name": "thth_analytics_report",
   "writes": false
  },
  {
   "annotations": {
    "destructiveHint": false,
    "openWorldHint": false,
    "readOnlyHint": true
   },
   "argv": {
    "always": [
     "--json"
    ],
    "flags": {},
    "positional": "name",
    "texts": {},
    "values": {}
   },
   "command": "approvals",
   "confirm": null,
   "description": "口座の承認の台帳を読む（name に口座）。読むだけ",
   "inputSchema": {
    "additionalProperties": false,
    "properties": {
     "name": {
      "maxLength": 64,
      "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$",
      "type": "string"
     }
    },
    "required": [
     "name"
    ],
    "type": "object"
   },
   "name": "thth_approvals",
   "writes": false
  },
  {
   "annotations": {
    "destructiveHint": false,
    "openWorldHint": false,
    "readOnlyHint": true
   },
   "argv": {
    "always": [
     "--json"
    ],
    "flags": {},
    "positional": null,
    "texts": {},
    "values": {}
   },
   "command": "board",
   "confirm": null,
   "description": "括りの一枚（口座ごとの未承認・予約・止まり・次の一手）を読む。読むだけ",
   "inputSchema": {
    "additionalProperties": false,
    "properties": {},
    "type": "object"
   },
   "name": "thth_board",
   "writes": false
  },
  {
   "annotations": {
    "destructiveHint": false,
    "openWorldHint": false,
    "readOnlyHint": true
   },
   "argv": {
    "always": [
     "--json"
    ],
    "flags": {},
    "positional": "name",
    "texts": {},
    "values": {
     "project": "--project"
    }
   },
   "command": "handoff-report",
   "confirm": null,
   "description": "申し送りの報告を読む（name か project で絞る）。読むだけ",
   "inputSchema": {
    "additionalProperties": false,
    "properties": {
     "name": {
      "maxLength": 64,
      "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$",
      "type": "string"
     },
     "project": {
      "maxLength": 64,
      "pattern": "^[A-Za-z0-9_.:+,-]{1,64}$",
      "type": "string"
     }
    },
    "type": "object"
   },
   "name": "thth_handoff_report",
   "writes": false
  },
  {
   "annotations": {
    "destructiveHint": false,
    "openWorldHint": false,
    "readOnlyHint": true
   },
   "argv": {
    "always": [
     "--json"
    ],
    "flags": {
     "counts_only": "--counts-only",
     "no_world": "--no-world"
    },
    "positional": "name",
    "texts": {},
    "values": {}
   },
   "command": "morning",
   "confirm": null,
   "description": "project の毎朝の一枚を読む（既読の印は付けない）。読むだけ",
   "inputSchema": {
    "additionalProperties": false,
    "properties": {
     "counts_only": {
      "type": "boolean"
     },
     "name": {
      "maxLength": 64,
      "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$",
      "type": "string"
     },
     "no_world": {
      "type": "boolean"
     }
    },
    "required": [
     "name"
    ],
    "type": "object"
   },
   "name": "thth_morning",
   "writes": false
  },
  {
   "annotations": {
    "destructiveHint": false,
    "openWorldHint": false,
    "readOnlyHint": true
   },
   "argv": {
    "always": [
     "--json"
    ],
    "flags": {
     "counts_only": "--counts-only",
     "no_world": "--no-world"
    },
    "positional": "name",
    "texts": {},
    "values": {}
   },
   "command": "observe",
   "confirm": null,
   "description": "project の観測の一枚を読む（既読の印は付けない）。読むだけ",
   "inputSchema": {
    "additionalProperties": false,
    "properties": {
     "counts_only": {
      "type": "boolean"
     },
     "name": {
      "maxLength": 64,
      "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$",
      "type": "string"
     },
     "no_world": {
      "type": "boolean"
     }
    },
    "required": [
     "name"
    ],
    "type": "object"
   },
   "name": "thth_observe",
   "writes": false
  },
  {
   "annotations": {
    "destructiveHint": false,
    "openWorldHint": false,
    "readOnlyHint": true
   },
   "argv": {
    "always": [
     "--json"
    ],
    "flags": {
     "unapproved": "--unapproved"
    },
    "positional": "name",
    "texts": {},
    "values": {
     "days": "--days",
     "window": "--window"
    }
   },
   "command": "schedule",
   "confirm": null,
   "description": "予約と未承認の一覧を読む（name に口座を入れるとその口座だけ）。読むだけ",
   "inputSchema": {
    "additionalProperties": false,
    "properties": {
     "days": {
      "maxLength": 64,
      "pattern": "^[A-Za-z0-9_.:+,-]{1,64}$",
      "type": "string"
     },
     "name": {
      "maxLength": 64,
      "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$",
      "type": "string"
     },
     "unapproved": {
      "type": "boolean"
     },
     "window": {
      "maxLength": 64,
      "pattern": "^[A-Za-z0-9_.:+,-]{1,64}$",
      "type": "string"
     }
    },
    "type": "object"
   },
   "name": "thth_schedule",
   "writes": false
  },
  {
   "annotations": {
    "destructiveHint": false,
    "openWorldHint": false,
    "readOnlyHint": true
   },
   "argv": {
    "always": [
     "--json"
    ],
    "flags": {},
    "positional": "name",
    "texts": {},
    "values": {
     "since": "--since"
    }
   },
   "command": "unanswered",
   "confirm": null,
   "description": "まだ返していない返信を読む（name に口座）。読むだけ",
   "inputSchema": {
    "additionalProperties": false,
    "properties": {
     "name": {
      "maxLength": 64,
      "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$",
      "type": "string"
     },
     "since": {
      "maxLength": 64,
      "pattern": "^[A-Za-z0-9_.:+,-]{1,64}$",
      "type": "string"
     }
    },
    "required": [
     "name"
    ],
    "type": "object"
   },
   "name": "thth_unanswered",
   "writes": false
  },
  {
   "annotations": {
    "destructiveHint": false,
    "openWorldHint": false,
    "readOnlyHint": true
   },
   "argv": {
    "always": [
     "--json"
    ],
    "flags": {},
    "positional": null,
    "texts": {},
    "values": {
     "medium": "--medium",
     "since": "--since"
    }
   },
   "command": "where-log",
   "confirm": null,
   "description": "どこに出たかの記録を読む。読むだけ",
   "inputSchema": {
    "additionalProperties": false,
    "properties": {
     "medium": {
      "enum": [
       "bluesky",
       "mastodon",
       "threads",
       "x"
      ],
      "type": "string"
     },
     "since": {
      "maxLength": 64,
      "pattern": "^[A-Za-z0-9_.:+,-]{1,64}$",
      "type": "string"
     }
    },
    "type": "object"
   },
   "name": "thth_where_log",
   "writes": false
  },
  {
   "annotations": {
    "destructiveHint": false,
    "openWorldHint": false,
    "readOnlyHint": false
   },
   "argv": {
    "always": [
     "--json"
    ],
    "flags": {},
    "positional": "files",
    "texts": {},
    "values": {
     "account": "--account",
     "approval": "--approval",
     "confirm": "--confirm",
     "wait": "--wait"
    }
   },
   "command": "approve",
   "confirm": "vm",
   "description": "原稿を承認する（2 段）。confirm を省くと本文の全文と digest を返すだけで何も書かない。その本文を持ち主に見せ、承認すると言われたら、同じ引数に digest を confirm として付けて呼ぶ",
   "inputSchema": {
    "additionalProperties": false,
    "properties": {
     "account": {
      "maxLength": 64,
      "pattern": "^[A-Za-z0-9_.:+,-]{1,64}$",
      "type": "string"
     },
     "approval": {
      "maxLength": 64,
      "pattern": "^[A-Za-z0-9_.:+,-]{1,64}$",
      "type": "string"
     },
     "confirm": {
      "description": "1 段目が返した digest（省くと 1 段目）",
      "maxLength": 64,
      "pattern": "^[A-Za-z0-9_.:+,-]{1,64}$",
      "type": "string"
     },
     "files": {
      "description": "原稿の、口座の repo の根からの相対パス",
      "items": {
       "maxLength": 300,
       "minLength": 1,
       "type": "string"
      },
      "maxItems": 20,
      "minItems": 1,
      "type": "array"
     },
     "wait": {
      "maxLength": 64,
      "pattern": "^[A-Za-z0-9_.:+,-]{1,64}$",
      "type": "string"
     }
    },
    "required": [
     "account",
     "files"
    ],
    "type": "object"
   },
   "name": "thth_approve",
   "writes": true
  },
  {
   "annotations": {
    "destructiveHint": true,
    "openWorldHint": false,
    "readOnlyHint": false
   },
   "argv": {
    "always": [
     "--json"
    ],
    "flags": {},
    "positional": "files",
    "texts": {
     "reason": "--reason"
    },
    "values": {
     "account": "--account",
     "wait": "--wait"
    }
   },
   "command": "revoke",
   "confirm": "worker",
   "description": "承認を取り消して draft に戻す（2 段）。confirm を省くと何をするかと確認の値を返すだけ。持ち主に見せて、そうすると言われたら同じ引数に confirm を付けて呼ぶ",
   "inputSchema": {
    "additionalProperties": false,
    "properties": {
     "account": {
      "maxLength": 64,
      "pattern": "^[A-Za-z0-9_.:+,-]{1,64}$",
      "type": "string"
     },
     "confirm": {
      "description": "1 段目が返した確認の値（省くと 1 段目）",
      "maxLength": 128,
      "type": "string"
     },
     "files": {
      "description": "原稿の、口座の repo の根からの相対パス",
      "items": {
       "maxLength": 300,
       "minLength": 1,
       "type": "string"
      },
      "maxItems": 20,
      "minItems": 1,
      "type": "array"
     },
     "reason": {
      "maxLength": 200,
      "minLength": 1,
      "type": "string"
     },
     "wait": {
      "maxLength": 64,
      "pattern": "^[A-Za-z0-9_.:+,-]{1,64}$",
      "type": "string"
     }
    },
    "required": [
     "account",
     "files"
    ],
    "type": "object"
   },
   "name": "thth_revoke",
   "writes": true
  },
  {
   "annotations": {
    "destructiveHint": true,
    "openWorldHint": false,
    "readOnlyHint": false
   },
   "argv": {
    "always": [
     "--json"
    ],
    "flags": {
     "undo": "--undo"
    },
    "positional": "files",
    "texts": {
     "reason": "--reason"
    },
    "values": {
     "account": "--account",
     "wait": "--wait"
    }
   },
   "command": "withdraw",
   "confirm": "worker",
   "description": "出さないと決めた draft を閉じる（undo で戻す・2 段）。confirm を省くと何をするかと確認の値を返すだけ。持ち主に見せて、そうすると言われたら同じ引数に confirm を付けて呼ぶ",
   "inputSchema": {
    "additionalProperties": false,
    "properties": {
     "account": {
      "maxLength": 64,
      "pattern": "^[A-Za-z0-9_.:+,-]{1,64}$",
      "type": "string"
     },
     "confirm": {
      "description": "1 段目が返した確認の値（省くと 1 段目）",
      "maxLength": 128,
      "type": "string"
     },
     "files": {
      "description": "原稿の、口座の repo の根からの相対パス",
      "items": {
       "maxLength": 300,
       "minLength": 1,
       "type": "string"
      },
      "maxItems": 20,
      "minItems": 1,
      "type": "array"
     },
     "reason": {
      "maxLength": 200,
      "minLength": 1,
      "type": "string"
     },
     "undo": {
      "type": "boolean"
     },
     "wait": {
      "maxLength": 64,
      "pattern": "^[A-Za-z0-9_.:+,-]{1,64}$",
      "type": "string"
     }
    },
    "required": [
     "account",
     "files"
    ],
    "type": "object"
   },
   "name": "thth_withdraw",
   "writes": true
  }
 ]
};
