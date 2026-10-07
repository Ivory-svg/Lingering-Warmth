# 接口

## HTTP 接口（第一档）

所有请求和响应都是 JSON。时间格式同 [schema.md](schema.md)。

### `POST /chat`

玩家发一条消息，拿到回复。

请求：

```json
{ "content": "今天好累" }
```

响应：

```json
{
  "message_id": 128,
  "reply": "……",
  "session_id": 7,
  "ooc_rewritten": true,
  "ooc": {
    "original": "我好想你！",
    "reason": "他从不用感叹号，也不会直接说想你",
    "voice_note_id": 12
  },
  "handoff_happened": false
}
```

- `ooc_rewritten`：发出前被守门人改写过
- `ooc`：只在 `ooc_rewritten` 为 true 时出现，否则为 null。前端点开小标记时显示这里的内容，不用再发请求
- `handoff_happened`：这一轮之前刚做过一次交接（前端可以显示一条细细的分隔线）

### `GET /messages`

网页打开或刷新时，拿回当前窗口的原话，好把对话和小标记重新画出来。

响应：数组，按时间顺序。

```json
[
  { "message_id": 127, "role": "user", "content": "今天好累", "created_at": "..." },
  {
    "message_id": 128, "role": "assistant", "content": "……", "created_at": "...",
    "ooc": { "original": "...", "reason": "...", "rewritten": "...", "voice_note_id": 12, "source": "gatekeeper" }
  }
]
```

- `ooc`：这条回复有过语感笔记（守门人改写或玩家标记）时才有，取最新一条；否则为 null
- `role = event` 的消息不返回
- 交接分隔线暂不需要在刷新后还原

### `POST /ooc/flag`

玩家觉得“他不会这么说”。

请求：

```json
{ "message_id": 128, "player_rewrite": null }
```

- `player_rewrite` 为空：守门人分析并给出改写
- `player_rewrite` 有值：直接用玩家改的版本，守门人只补写原因

响应：

```json
{
  "verdict": "ooc",
  "reason": "他不会直接说想你，会绕个弯",
  "rewritten": "……",
  "voice_note_id": 12
}
```

`verdict` 为 `in_character` 时 `rewritten` 和 `voice_note_id` 为空，并告诉玩家守门人觉得这句没问题；玩家仍可传 `player_rewrite` 强制存一条。

### `GET /voice-notes` / `PATCH /voice-notes/{id}`

列出语感笔记；`PATCH` 只允许改 `active`。

### `POST /voice-notes/{id}/overturn`

玩家点开小标记，觉得守门人看走眼了：“其实他会这么说”。

请求：

```json
{ "note": "他就是会这么直接，这是他因为你变的" }
```

`note` 可空。

做三件事：

1. 这条语感笔记设 `active = 0`，并写上 `overturned_at`、`overturn_note`
2. 如果是守门人改写的（`source = gatekeeper`），这条回复恢复成原句：在消息 `meta` 里记 `restored`，见 [schema.md](schema.md)
3. 返回恢复后要显示的内容

响应：

```json
{ "message_id": 128, "content": "我好想你！", "restored": true }
```

- 玩家标记产生的笔记（`source = player`）被推翻时，只停用笔记，回复本来就没改过，`restored` 为 false
- 已经推翻过的再推翻，直接返回当前状态，不报错
- 推翻后小标记消失，这条回复看起来和普通回复一样

### `GET /export`

完整导出：所有表的所有行，一个 JSON 文件。世界书文件不在数据库里，不导出。

```json
{
  "exported_at": "...",
  "schema_version": "1",
  "tables": { "sessions": [...], "messages": [...], "memories": [...], "...": [...] }
}
```

`embedding` 字段导出为 base64。

### `GET /health`

返回 `{"ok": true, "schema_version": "1"}`。

---

## 模块接口

只描述职责和输入输出，具体函数名可由实作者决定，但要保持这些边界。

### llm

| 能力 | 输入 | 输出 |
|---|---|---|
| 对话 | messages 列表、是否用小模型、可选 `response_format=json` | 文本 |
| 估算 token | 文本 | 整数。先用 `字符数 ÷ 1.5` 粗估，中文够用 |
| embedding（第二档） | 文本 | float 数组 |

- 失败重试 2 次，指数退避。
- 要求 JSON 输出时，解析失败重试一次；还失败就走各模块写的降级路径，不让一轮对话崩掉。

### worldbook

世界书目录结构（见 design.md「世界书拆成三份」）：

```
worldbook/
├── core.md          # 核心，每轮都带
├── corpus.md        # 语料，一行一句，空行和 # 开头的行忽略
├── daily.md         # 日常作息（可选，第三档用）
└── entries/         # 词条，一件事一个 .md
    └── 父亲.md
```

词条文件开头用 front matter 写关键词：

```markdown
---
keys: 爸爸, 父亲, 钟表店
priority: 5
---
他父亲是钟表匠，在他十九岁时去世……
```

- `keys`：逗号分隔，中英文逗号都认。必填，没写 keys 的词条不会被带入，并在启动日志里提醒
- `priority`：整数，默认 0，越大越先带
- 不用引入 YAML 库，只解析这两个字段

| 能力 | 输入 | 输出 |
|---|---|---|
| 核心 | — | `core.md` 全文。文件不存在时报错，启动失败 |
| 匹配词条 | 最近 `worldbook.scan_depth` 条原话（默认 4，玩家和他的都算） | 命中的词条，按 priority 从高到低；总字数不超过 `worldbook.entry_budget`（默认 3000），超出的丢掉低优先级的 |
| 抽语料 | — | 随机 `worldbook.corpus_sample` 句（默认 8）；语料不够就全给 |
| 缓存 | 文件修改时间变了才重读 | |

匹配规则：关键词在原话里出现就算命中，大小写不敏感，不分词。

没有写入能力。

**旧格式兼容**：如果 `worldbook/` 下没有 `core.md`，但有 `character.prompt_file` 指向的文件（例如旧的 `character.md`），就把它当核心；`worldbook/` 根目录下其他没有 front matter 的 `.md` 每轮都带，并在启动日志里建议拆成词条。

### context

输入：当前 session。输出：发给模型的 messages 列表。拼接顺序：

1. system：世界书核心
2. system：命中的词条（没有就跳过），每条前面标上词条名
3. system：抽到的语料，前面写一句“参考这些句子的感觉，用他的方式说新的话，不要原句复述”（没有语料就跳过）
4. system：语感笔记（最近 K 条 active 的），格式“原来想说：…… / 他会说：…… / 因为：……”
5. system：交接摘要（如果本 session 接续了交接）
6. 上一窗最后 N 条原话（如果有交接）
7. 本 session 的所有原话
8. 第二档起：浮现的记忆插在 5 和 6 之间

匹配词条用的“最近几句”，包含玩家刚发的这一句。

### inflow（流入）

| 输入 | 这一轮的玩家原话 + AI 回复（及其 message id） |
|---|---|
| 做什么 | 用 [prompts/inflow.md](prompts/inflow.md) 调小模型，得到 JSON，写一条 `kind=turn` 的记忆 |
| 降级 | JSON 解析失败时：summary 存原话前 100 字，emotion_text 存“未标注”，certainty 0.5 |
| 时机 | 回复发出后在后台跑，不阻塞玩家 |
| 攒几轮 | `inflow.every_n_turns`（默认 1）。设成 N 时，每攒满 N 轮才调一次小模型，把这 N 轮总结成一条记忆，`source_message_ids` 包含这 N 轮的所有原话。交接前没攒满的也要先总结掉，不能丢 |

小模型返回格式：

```json
{
  "summary": "她说今天加班很累，他让她先去吃饭",
  "quote": "今天好累",
  "emotion_text": "疲惫，被关心后放松了一点",
  "valence": 0.4,
  "arousal": 0.3,
  "certainty": 0.9
}
```

### handoff（交接）

| 能力 | 说明 |
|---|---|
| 是否需要交接 | 拼好的上下文 token 估算 ≥ `context_limit × trigger_ratio`，**并且**本 session 里玩家已经发了至少 `handoff.min_turns`（默认 6）句。第一个窗口不受这个限制 |
| 写交接 | 用 [prompts/handoff.md](prompts/handoff.md) 让**对话模型本身**（不是小模型）写，因为语气要是他的 |
| 落库 | 存 handoffs；写一条 `kind=handoff` 的记忆；关闭旧 session；开新 session |
| 带入 | 由 context 负责，见上 |

交接模型返回格式：

```json
{
  "doing": "陪她等外卖，顺便聊她明天的面试",
  "where_we_are": "她刚说有点紧张，他说要帮她模拟一遍",
  "emotion": "有点心疼，也想让她放松下来"
}
```

### gatekeeper（OOC 守门人）

| 输入 | 一句回复 + 世界书核心和本轮命中的词条（不带语料）+ 重铸层变化（第一档为空）+ 最近几句上下文 |
|---|---|
| 输出 | verdict、reason、rewritten |
| 用小模型 | 是 |
| 降级 | 解析失败或超时，视为 `in_character`，原样发出。守门人坏了不能让他说不出话 |

返回格式：

```json
{
  "verdict": "ooc",
  "reason": "他从不用感叹号，也不会直接说想你",
  "rewritten": "……"
}
```

规则（写在 [prompts/ooc-gatekeeper.md](prompts/ooc-gatekeeper.md) 里）：

- 只判断像不像他，不判断好不好
- 重铸层里的变化不算 OOC
- 拿不准时判 `in_character`

### export

读所有表，按上面格式输出。不做任何过滤，封存的也导出（那是玩家自己的东西）。

**Markdown 导出**（`GET /export?format=markdown`）：给人读的版本，一个 `.md` 文件。

```markdown
# 余温导出 · 2026-10-07

## 对话

### 窗口 7 · 2026-10-07 20:15 起
**你** 20:15
今天好累

**他** 20:15
……吃饭了没。

—— 接续上一段对话 ——

## 记忆
- 2026-10-07 20:15 · 疲惫，被关心后放松了一点 · 她说今天加班很累，他让她先去吃饭（确定度 0.9）

## 交接
## 语感笔记
```

- 称呼用 `character.name` 和 `character.player_name`
- 被推翻（`restored`）的回复显示原句
- 不导出 embedding
- 网页的“导出全部记忆”给两个选项：给人看的（Markdown）、给程序搬家的（JSON）

---

## 预留接口（不在第一档实作，只是先占位）

| 接口 | 用途 | 档 |
|---|---|---|
| `POST /events` | 感应事件（戳一下、摸头），写成 `role=event` 的消息 | 二 |
| `GET /diary?month=2026-10` / `POST /diary/{id}/reply` | 日记日历与回复 | 二 |
| `POST /memories/{id}/seal` | “这个忘掉吧” | 二 |
| `GET /wake/pending` | 前端轮询唤醒消息（或改成 WebSocket 推送） | 三 |
| TTS | 只定义 `text → 音频 URL` 一个接口，不自带实现 | 以后 |
| 生图 / 生视频 | 只定义 `描述 + 参考图 → 图片或视频 URL` 一个接口，不自带实现；报备用 | 以后 |
