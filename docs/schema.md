# 表结构（SQLite）

> 各部分的新名字（扉页、随笔、翻阅、续写、校对……）和旧名的对照，见 [design.md](design.md)「名字对照」。这份文档对着代码写，英文标识符不变；正文里的中文叫法会随代码一起换。

时间一律存 ISO 8601 字符串，带时区（例：`2026-10-07T20:15:00+08:00`）。
id 一律用 `INTEGER PRIMARY KEY`。JSON 字段存 TEXT。

建表放在一个迁移文件里，`schema_meta` 记版本号，启动时按版本号往上迁。

---

## 第一档

### schema_meta

| 字段 | 类型 | 说明 |
|---|---|---|
| key | TEXT PK | 固定为 `version` |
| value | TEXT | 当前 schema 版本 |

### sessions：卷

一个 session 就是一卷上下文。续写时关闭旧卷、开新卷。

| 字段 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK | |
| started_at | TEXT NOT NULL | |
| ended_at | TEXT | 续写后填 |
| continues_handoff_id | INTEGER | 接续哪一份续写摘要，第一卷为空 |

### messages：原话

所有说过的话，原样保存，永不改写。

| 字段 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK | |
| session_id | INTEGER NOT NULL | → sessions.id |
| role | TEXT NOT NULL | `user` / `assistant` / `event` |
| content | TEXT NOT NULL | 原文。被校对改写时，这里存实际发出的版本 |
| created_at | TEXT NOT NULL | |
| meta | TEXT | JSON，可选。如 `{"ooc_rewritten": true, "voice_note_id": 3}`；第二档起，回复上还有 `surfaced_memory_ids`：这一轮递给他的记忆 id；M3.1 起，来信是 `role = assistant` 的消息，meta 里有 `{"letter": true, "wake_log_id": 5}` |

`event` 留给第二档以后的感应事件（戳一下、摸头），第一档不用。

### memories：河流

随笔写这里。第二档以后翻阅、褪墨、修订都操作这张表。

| 字段 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK | |
| kind | TEXT NOT NULL | `turn`（每轮随笔）/ `handoff`（续写时的情绪）；以后加 `bubble` `touch` `dream` |
| created_at | TEXT NOT NULL | 写入时间 |
| happened_at | TEXT NOT NULL | 事情发生的时间（一般等于这一轮最后一条消息的时间） |
| summary | TEXT NOT NULL | 这一轮发生了什么，第三人称，一两句 |
| quote | TEXT | 最能代表这一轮的一句原话，可空 |
| emotion_text | TEXT NOT NULL | 情绪的文字描述，如“有点委屈，后来被哄好了” |
| valence | REAL | 愉悦度 0–1，可空 |
| arousal | REAL | 激动度 0–1，可空 |
| certainty | REAL NOT NULL | 有多确定 0–1。写随笔时默认 0.9；翻阅时低于阈值要说“好像是” |
| source_message_ids | TEXT NOT NULL | JSON 数组，追溯到原话 |
| session_id | INTEGER NOT NULL | → sessions.id |
| sealed | INTEGER NOT NULL DEFAULT 0 | 1 = 封页（“这个忘掉吧”），平时不翻阅 |
| weight | REAL NOT NULL DEFAULT 1.0 | 第二档褪墨用，第一档只写不读 |
| last_recalled_at | TEXT | 第二档用 |
| embedding | BLOB | 第二档用，float32 小端数组 |
| embedding_model | TEXT | 算向量用的模型名；换模型后据此重算（迁移 003 新增） |
| recall_count | INTEGER NOT NULL DEFAULT 0 | 翻到且真的用上后想起的次数，褪墨计算用（迁移 003 新增） |
| status | TEXT | 待续用。为空 = 普通记忆；`open` 待续 / `done` 完结 / `dropped` 不做了（迁移 005 新增） |
| status_as_of | TEXT | 这个状态是哪天的。记下时 = 这批最后一条消息的时间；改状态时更新（迁移 005 新增） |
| resolved_at | TEXT | 完结或不做了的时间；重新打开时清空（迁移 005 新增） |
| resolved_note | TEXT | 为什么完结，例如“阿栀说牙看完了”；重新打开时清空（迁移 005 新增） |

情绪先文字 + 数值两样都存，数值可空。以后哪一样没用上再删。

迁移 005 同时建索引 `memories(status)`。待续不新开 `kind`，仍是 `turn`；靠 `status` 不为空区分。

`weight` 保留不删，但第二档的褪墨不读它：“还记得多少”在翻阅时现算，见 interfaces.md 的 surfacing。

### memory_history：改写历史

任何对 memories 的修改，改之前先把整行（不含 `embedding`）存进来。目录里玩家改正、封页、撕开时开始用；第三档修订时也写这里。

| 字段 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK | |
| memory_id | INTEGER NOT NULL | → memories.id |
| snapshot | TEXT NOT NULL | 修改前整行的 JSON |
| changed_at | TEXT NOT NULL | |
| reason | TEXT | 为什么改 |

### auth_sessions：登录（迁移 004）

一台设备登录一次就是一行。只存 cookie 值的 sha256，不存原文。

| 字段 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK | |
| token_hash | TEXT NOT NULL UNIQUE | cookie 值的 sha256（十六进制） |
| password_fingerprint | TEXT NOT NULL | `sha256("yuwen-session:" + 密码)`，换密码后对不上就失效 |
| created_at | TEXT NOT NULL | |
| expires_at | TEXT NOT NULL | 创建时间 + `auth.session_days` |
| last_seen_at | TEXT NOT NULL | 最多一小时更新一次 |

这张表的行可以真删：登出、过期、换密码后对不上，都直接删。它不是记忆。

`GET /export` 不导出这张表。

### wake_log：来信记录（迁移 006）

每次“看一眼要不要写”留一行，写了没写都留。

| 字段 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK | |
| checked_at | TEXT NOT NULL | 看的时间 |
| decided | TEXT NOT NULL | `send` 写了 / `skip` 他决定不写 / `blocked` 规矩挡下，没问模型 |
| reason | TEXT NOT NULL | 为什么。`blocked` 写是哪条规矩，例如“不在白天”“上一封还没回” |
| message_id | INTEGER | 写了的话，→ messages.id |
| notified | INTEGER NOT NULL DEFAULT 0 | 1 = 通知发出去了（对方回 2xx） |

`GET /export` 导出这张表。

### anniversaries：纪年（迁移 007）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK | |
| month_day | TEXT NOT NULL | `MM-DD`，例如 `03-14` |
| year | INTEGER | 知道是哪一年就填，用来算“第几年”；不知道为空 |
| yearly | INTEGER NOT NULL DEFAULT 1 | 1 = 每年都过；0 = 只有那一次（这时 `year` 必填） |
| title | TEXT NOT NULL | 例如“第一次见面”“阿栀的生日” |
| source | TEXT NOT NULL | `conversation` 随笔里认出来的 / `manual` 玩家自己加的；以后加 `keepsake` |
| memory_id | INTEGER | 从哪条随笔认出来的，→ memories.id；手动加的为空 |
| active | INTEGER NOT NULL DEFAULT 1 | 0 = 停用，不再提醒 |
| created_at | TEXT NOT NULL | |
| updated_at | TEXT NOT NULL | |

纪年不是记忆，可以改，但没有删除接口：不要了就停用。`GET /export` 导出这张表。

### handoffs：续写（交接）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK | |
| session_id | INTEGER NOT NULL | 被关闭的那个窗口 |
| created_at | TEXT NOT NULL | |
| doing | TEXT NOT NULL | 现在在做什么 |
| where_we_are | TEXT NOT NULL | 聊到哪了 |
| emotion | TEXT NOT NULL | 此刻的情绪 |
| raw_tail_message_ids | TEXT NOT NULL | JSON 数组，新窗口要带上的最后 N 条原话 |

### voice_notes：勘误（语感笔记）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK | |
| created_at | TEXT NOT NULL | |
| source | TEXT NOT NULL | `gatekeeper`（发出前自查）/ `player`（玩家标记） |
| message_id | INTEGER | 对应哪条回复 |
| original | TEXT NOT NULL | 原句 |
| rewritten | TEXT NOT NULL | 改后的句子 |
| reason | TEXT NOT NULL | 为什么不像他 |
| active | INTEGER NOT NULL DEFAULT 1 | 玩家可以停用某条 |
| overturned_at | TEXT | 玩家说“其实他会这么说”的时间；为空表示没被推翻（迁移 002 新增） |
| overturn_note | TEXT | 玩家推翻时留的话，可空（迁移 002 新增） |

**校对看走眼的记录**：`source = gatekeeper` 且 `overturned_at` 不为空的勘误，就是校对判错的例子。以后调校对的 prompt 时拿来对照，不单独建表。

**推翻后原话怎么办**：`messages.content` 仍然不改。推翻时在这条消息的 `meta` 里记 `{"restored": true, "voice_note_id": 12}`；拼上下文和 `GET /messages` 时，遇到 `restored` 就改用这条勘误的 `original`。这样“原话永不改写”的规则不破，推翻也随时可以追溯。

---

## 第二档以后（先不建，写在这里方便提前对齐）

| 表 | 用途 | 关键字段 |
|---|---|---|
| diary_entries | 折角 | date, title, content, written_at, memory_ids |
| diary_replies | 玩家在折角下的回复 | entry_id, content, created_at, read_by_ai_at |
| keepsakes | 夹页 | name, aliases(JSON), first_seen_at |
| keepsake_links | 夹页 ↔ 记忆 | keepsake_id, memory_id |
| dream_books | 留白合集，按月一本 | month(YYYY-MM), created_at, updated_at |
| dreams | 留白合集里的一个梦（追加，不单独翻阅） | book_id, created_at, based_on_memory_id, content, used_in_wake |
| player_days | 玩家的日子（经期等，默认关闭） | date, kind, note, source |
