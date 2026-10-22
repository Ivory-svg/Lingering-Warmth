# 参考实现：架构

> 各部分的新名字（扉页、随笔、翻阅、续写、校对……）和旧名的对照，见 [design.md](design.md)「名字对照」。这份文档对着代码写，英文标识符不变；正文里的中文叫法会随代码一起换。

这份文档描述参考实现长什么样。设计意图见 [design.md](design.md)，表结构见 [schema.md](schema.md)，接口见 [interfaces.md](interfaces.md)。

## 原则

1. **记忆搭在后端主程序里，不做成 MCP。** 随笔、续写、留白、来信都不是由模型调用触发的，而是由程序在对话前后、定时任务里触发。玩家多数接的是 DeepSeek 等 API，不一定支持工具调用。
2. **只依赖 SQLite。** 一个文件就是全部记忆，方便导出、备份、搬家。
3. **模型接口用 OpenAI 兼容格式。** DeepSeek、通义、Kimi、OpenAI 等都能接，只改 `base_url`、`api_key`、`model`。
4. **扉页只读。** 程序任何地方都不写扉页。
5. **只增不删。** 记忆修订存历史，“忘掉”是封页。唯一的真删除是玩家主动要求的导出后清空。
6. **仓库里不放任何人的私人内容。** 扉页、记忆、数据库都只放示例。

## 技术选型

| 项 | 选择 | 理由 |
|---|---|---|
| 语言 | Python 3.11+ | 玩家最容易看懂、最容易改 |
| Web 框架 | FastAPI | 自带接口文档页，前端好对接 |
| 数据库 | SQLite（标准库 `sqlite3`） | 零部署 |
| HTTP 客户端 | httpx | 调模型 API |
| 配置 | 一个 `config.toml` | 玩家只改这一个文件 |
| 向量检索（第二档） | 先用 numpy 暴力算余弦相似度 | 个人记忆量小，几万条以内够用；以后再换 sqlite-vec |

## 目录结构

```
lingering-warmth/
├── config.example.toml        # 配置示例，玩家复制成 config.toml
├── worldbook/                 # 玩家自己的扉页（.gitignore，不进仓库）
├── examples/
│   └── worldbook/             # 示例扉页：core.md、corpus.md、daily.md、entries/
├── data/                      # SQLite 数据库（.gitignore）
├── src/yuwen/
│   ├── config.py              # 读 config.toml
│   ├── llm.py                 # OpenAI 兼容的对话 / embedding 客户端
│   ├── store.py               # 所有 SQLite 读写，其他模块不直接写 SQL
│   ├── worldbook.py           # 读扉页（只读）
│   ├── context.py             # 拼装每轮发给模型的上下文
│   ├── chat.py                # 一轮对话的总流程
│   ├── inflow.py              # 随笔：每轮存一条记忆
│   ├── embedder.py            # 新记忆算向量、启动时补算
│   ├── surfacing.py           # 翻阅：相似检索与褪墨现算
│   ├── handoff.py             # 续写：写摘要、新窗口带入
│   ├── gatekeeper.py          # OOC 校对与勘误
│   ├── export.py              # 完整导出
│   ├── auth.py                # 登录：密码比对、会话、输错限制、默认全锁的中间件
│   ├── catalog.py             # 目录：列记忆、改正、封页（褪墨用 surfacing 的同一个函数）
│   ├── api.py                 # HTTP 接口
│   ├── static/                # index.html（聊天）、catalog.html（目录）、login.html（登录）
│   └── prompts/               # 运行时用的 prompt 文本（从 docs/prompts 复制）
│   # 第二档后续再加：diary.py keepsakes.py
│   # 第三档以后再加：recast.py dream.py wake.py anniversaries.py
└── tests/
```

## 一轮对话的流程（第一档）

```
玩家发消息
  │
  ▼
chat.handle_message
  1. store：存玩家原话（messages）
  2. handoff：估算本卷 token，超过阈值就先续写，开新的一卷
  3. context：拼上下文
       角色 prompt（含扉页）
       + 勘误（最近 K 条）
       + 续写摘要（如果本卷是接续的）
       + 本卷的原话
  4. llm：生成回复
  5. gatekeeper（config 打开时）：自查
       像他 → 原样发出
       不像 → 用改写版发出，存一条勘误
  6. store：存 AI 回复
  7. 返回回复给前端
  │
  ▼（回复发出后，后台执行，不阻塞玩家）
inflow：把这一轮（玩家原话 + 回复）记成一条随笔，标情绪和确定度
```

## 来信的流程（M3.1）

```
后台任务按随机间隔醒来
  1. 规矩：白天？刚聊过？今天写够了？上一封没回？ → 挡下就记一行，回去睡
  2. context：和平时一样拼上下文，最后加 prompts/wake.md
  3. llm（对话模型）：决定写不写，写什么
  4. gatekeeper（打开时）：校对
  5. store：信存成这一卷的一条他的话（meta.letter），wake_log 记一行
  6. 通知：往 wake.notify_url 发一下（失败不影响）
  7. 排下一次
网页开着时每分钟 GET /messages?after_id=… 拿新的信
```

## 续写（交接）的流程

```
本卷 token 估算 ≥ 上限 × handoff.trigger_ratio
  1. 用 docs/prompts/handoff.md 让 AI 自己写续写摘要：
     在做什么、聊到哪了、此刻的情绪
  2. store：存 handoff，关闭当前 session
  3. 续写时的情绪也写一条记忆（kind = handoff）
  4. 新建 session，记下它接续的 handoff
新的一卷拼上下文时：续写摘要 + 上一卷最后 N 条原话
```

N 默认 20，可在 config 里改。

## 模块之间的依赖

```
api → chat → context → worldbook, store
           → llm
           → gatekeeper → llm, store, worldbook
           → handoff → llm, store
           → inflow → llm, store
           → surfacing → store, llm（可选 embedding）
api → embedder → store, llm（可选 embedding）
api → export → store
```

- 只有 `store.py` 写 SQL。
- 只有 `llm.py` 发网络请求。
- `worldbook.py` 只读文件。

## 配置项（config.example.toml 里要有）

| 键 | 默认 | 说明 |
|---|---|---|
| `llm.base_url` | — | OpenAI 兼容地址 |
| `llm.api_key` | — | |
| `llm.model` | — | 对话模型 |
| `llm.small_model` | 同 `llm.model` | 随笔、校对用的小模型，省钱 |
| `llm.context_limit` | 64000 | 模型上下文上限（token） |
| `character.name` | `他` | 角色名，用在校对、随笔的 prompt 和 Markdown 导出里 |
| `character.player_name` | `对方` | 给模型看的文字里怎么称呼玩家：摘要、续写、校对。建议填他平时叫你的昵称。不要填“你”“我”：模型会把“你”当成它自己 |
| `character.prompt_file` | `worldbook/core.md` | 扉页核心（旧配置指向 `character.md` 也认） |
| `character.worldbook_dir` | `worldbook/` | 扉页目录，结构见 interfaces.md |
| `worldbook.scan_depth` | 4 | 匹配词条时看最近几条原话 |
| `worldbook.entry_budget` | 3000 | 每轮带入词条的总字数上限 |
| `worldbook.corpus_sample` | 8 | 每轮抽几句语料 |
| `handoff.trigger_ratio` | 0.75 | 用到上限的多少开始续写 |
| `handoff.raw_tail` | 20 | 新的一卷带上一卷多少条原话 |
| `handoff.min_turns` | 20 | 换卷后玩家至少说几句，才允许再次续写。只是防止连续换卷的保险，正常要几百句才会换一次卷 |
| `inflow.every_n_turns` | 1 | 每几轮总结一次记忆；调大省钱 |
| `embedding.base_url` | 空 | OpenAI 兼容的 embedding 地址；留空 = 不用 embedding，翻阅退回字面匹配 |
| `embedding.api_key` | 空 | |
| `embedding.model` | `bge-m3` | |
| `embedding.batch_size` | 32 | 补算时一批几条 |
| `surfacing.enabled` | true | 关掉就不翻阅 |
| `surfacing.query_depth` | 2 | 拿最近几条原话去找 |
| `surfacing.top_k` | 4 | 一次最多递几条 |
| `surfacing.max_chars` | 1500 | 递过去的总字数上限 |
| `surfacing.min_similarity` | 0.45 | 按意思找时的门槛；字面匹配固定用 0.2 |
| `surfacing.clear_threshold` | 0.7 | 确定度到多少算“记得清楚” |
| `decay.base_days` | 7 | 褪墨的基础天数；越大淡得越慢 |
| `todo.enabled` | true | 待续：写随笔时记下、完结答应过的事，每轮带上还没完结的。不多调模型 |
| `todo.max_in_context` | 3 | 每轮最多带几件还没完结的事 |
| `todo.cooldown_hours` | 12 | 他问过一件以后，歇几个小时再带 |
| `todo.stale_days` | 30 | 超过几天的待续不再每轮带，翻到时说“这是很久以前的状态” |
| `todo.max_open_in_prompt` | 10 | 写随笔时给小模型看几件还没完结的事 |
| `forget.enabled` | true | 对话里说“忘掉吧”就封页。不多调模型 |
| `forget.recent_memories` | 5 | 写随笔时，除了递过的往事，再给小模型看这一卷最近几条随笔供挑选 |
| `anniversary.enabled` | true | 纪年：写随笔时记下重要的日子，到了那天他记得。不多调模型 |
| `wake.enabled` | true | 来信：他会不会先来找你。关掉就不看、不写、不多花钱 |
| `wake.timezone` | 空 | 判断白天、今天用的时区，例如 `Asia/Shanghai`；空 = 服务器本地时间 |
| `wake.active_hours` | `09:00-23:00` | 只在这段时间里看要不要写 |
| `wake.min_interval_minutes` / `wake.max_interval_minutes` | 40 / 120 | 多久看一眼，在这两个数之间随机 |
| `wake.min_silence_minutes` | 60 | 最后一句话不到这么久，不写 |
| `wake.max_per_day` | 3 | 一天最多写几封 |
| `wake.max_unanswered` | 2 | 连着几封没回，就不再写，等玩家说话 |
| `wake.notify_url` | 空 | 写了信往哪里发通知；空 = 不发。填 ntfy 频道地址就能推到手机 |
| `wake.notify_format` | `text` | `text` 发纯文本（ntfy 用）；`json` 发详细内容（自己的程序用） |
| `gatekeeper.pre_send` | true | 发出前是否自查 |
| `gatekeeper.voice_notes_in_context` | 10 | 拼上下文时带几条勘误 |
| `storage.db_path` | `data/yuwen.db` | |
| `server.host` / `server.port` | 127.0.0.1 / 8787 | |
| `auth.password` | — | 必填，至少 8 位。没设、还是占位文字或太短，就不启动 |
| `auth.session_days` | 30 | 登录一次记住几天 |
| `auth.secure_cookie` | false | 放在 https 后面时设成 true，cookie 只走 https |

**embedding 怎么配**（二选一，想配就配）：

| 选择 | 怎么填 | 说明 |
|---|---|---|
| 不配 | `embedding.base_url` 留空 | 翻阅按字面找。不多花钱，不用多注册 |
| 在线平台 | 填平台给的地址、key，模型选 bge-m3 | 例如 SiliconFlow、OpenRouter 等提供 bge-m3 的平台。按量计费，很便宜，价格和免费额度以平台为准 |

对话模型（例如 DeepSeek）不一定提供 embedding，所以单独配置。

进阶：任何 OpenAI 兼容的 `/embeddings` 地址都能用，包括在自己电脑上用 Ollama 跑 bge-m3（`http://localhost:11434/v1`）。教程不教这一条。
