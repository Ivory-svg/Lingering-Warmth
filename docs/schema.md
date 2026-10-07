# 表结构（SQLite）

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

### sessions：窗口

一个 session 就是一个上下文窗口。交接时关闭旧的、开新的。

| 字段 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK | |
| started_at | TEXT NOT NULL | |
| ended_at | TEXT | 交接后填 |
| continues_handoff_id | INTEGER | 接续哪一份交接，第一个窗口为空 |

### messages：原话

所有说过的话，原样保存，永不改写。

| 字段 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK | |
| session_id | INTEGER NOT NULL | → sessions.id |
| role | TEXT NOT NULL | `user` / `assistant` / `event` |
| content | TEXT NOT NULL | 原文。被守门人改写时，这里存实际发出的版本 |
| created_at | TEXT NOT NULL | |
| meta | TEXT | JSON，可选。如 `{"ooc_rewritten": true, "voice_note_id": 3}` |

`event` 留给第二档以后的感应事件（戳一下、摸头），第一档不用。

### memories：河流

流入写这里。第二档以后浮现、遗忘、重铸都操作这张表。

| 字段 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK | |
| kind | TEXT NOT NULL | `turn`（每轮流入）/ `handoff`（交接时的情绪）；以后加 `bubble` `touch` `dream` |
| created_at | TEXT NOT NULL | 写入时间 |
| happened_at | TEXT NOT NULL | 事情发生的时间（一般等于这一轮最后一条消息的时间） |
| summary | TEXT NOT NULL | 这一轮发生了什么，第三人称，一两句 |
| quote | TEXT | 最能代表这一轮的一句原话，可空 |
| emotion_text | TEXT NOT NULL | 情绪的文字描述，如“有点委屈，后来被哄好了” |
| valence | REAL | 愉悦度 0–1，可空 |
| arousal | REAL | 激动度 0–1，可空 |
| certainty | REAL NOT NULL | 有多确定 0–1。流入时默认 0.9；以后浮现时低于阈值要说“好像是” |
| source_message_ids | TEXT NOT NULL | JSON 数组，追溯到原话 |
| session_id | INTEGER NOT NULL | → sessions.id |
| sealed | INTEGER NOT NULL DEFAULT 0 | 1 = 封存（“这个忘掉吧”），平时不浮现 |
| weight | REAL NOT NULL DEFAULT 1.0 | 第二档遗忘用，第一档只写不读 |
| last_recalled_at | TEXT | 第二档用 |
| embedding | BLOB | 第二档用，float32 数组 |

情绪先文字 + 数值两样都存，数值可空。以后哪一样没用上再删。

### memory_history：改写历史

任何对 memories 的修改，改之前先把整行存进来。第一档只建表，第三档重铸时开始用。

| 字段 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK | |
| memory_id | INTEGER NOT NULL | → memories.id |
| snapshot | TEXT NOT NULL | 修改前整行的 JSON |
| changed_at | TEXT NOT NULL | |
| reason | TEXT | 为什么改 |

### handoffs：交接

| 字段 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK | |
| session_id | INTEGER NOT NULL | 被关闭的那个窗口 |
| created_at | TEXT NOT NULL | |
| doing | TEXT NOT NULL | 现在在做什么 |
| where_we_are | TEXT NOT NULL | 聊到哪了 |
| emotion | TEXT NOT NULL | 此刻的情绪 |
| raw_tail_message_ids | TEXT NOT NULL | JSON 数组，新窗口要带上的最后 N 条原话 |

### voice_notes：语感笔记

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

---

## 第二档以后（先不建，写在这里方便提前对齐）

| 表 | 用途 | 关键字段 |
|---|---|---|
| diary_entries | 日记 | date, title, content, written_at, memory_ids |
| diary_replies | 玩家在日记下的回复 | entry_id, content, created_at, read_by_ai_at |
| keepsakes | 信物 | name, aliases(JSON), first_seen_at |
| keepsake_links | 信物 ↔ 记忆 | keepsake_id, memory_id |
| anniversaries | 纪念日 | date(MM-DD 或完整日期), title, source(`manual`/`keepsake`), memory_id |
| dreams | 梦境产生的想法 | created_at, based_on_memory_id, content, used_in_wake |
| wake_log | 唤醒记录 | checked_at, decided(`send`/`skip`), reason, message_id |
| player_days | 玩家的日子（经期等，默认关闭） | date, kind, note, source |
