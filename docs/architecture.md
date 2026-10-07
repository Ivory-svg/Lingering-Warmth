# 参考实现：架构

这份文档描述参考实现长什么样。设计意图见 [design.md](design.md)，表结构见 [schema.md](schema.md)，接口见 [interfaces.md](interfaces.md)。

## 原则

1. **记忆搭在后端主程序里，不做成 MCP。** 流入、交接、梦境、唤醒都不是由模型调用触发的，而是由程序在对话前后、定时任务里触发。玩家多数接的是 DeepSeek 等 API，不一定支持工具调用。
2. **只依赖 SQLite。** 一个文件就是全部记忆，方便导出、备份、搬家。
3. **模型接口用 OpenAI 兼容格式。** DeepSeek、通义、Kimi、OpenAI 等都能接，只改 `base_url`、`api_key`、`model`。
4. **地基只读。** 程序任何地方都不写世界书。
5. **只增不删。** 记忆改写存历史，“忘掉”是封存。唯一的真删除是玩家主动要求的导出后清空。
6. **仓库里不放任何人的私人内容。** 世界书、记忆、数据库都只放示例。

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
├── worldbook/                 # 玩家自己的世界书（.gitignore，不进仓库）
├── examples/
│   └── worldbook.example.md   # 示例世界书
├── data/                      # SQLite 数据库（.gitignore）
├── src/yuwen/
│   ├── config.py              # 读 config.toml
│   ├── llm.py                 # OpenAI 兼容的对话 / embedding 客户端
│   ├── store.py               # 所有 SQLite 读写，其他模块不直接写 SQL
│   ├── worldbook.py           # 读世界书（只读）
│   ├── context.py             # 拼装每轮发给模型的上下文
│   ├── chat.py                # 一轮对话的总流程
│   ├── inflow.py              # 流入：每轮存一条记忆
│   ├── handoff.py             # 交接：写交接、新窗口带入
│   ├── gatekeeper.py          # OOC 守门人与语感笔记
│   ├── export.py              # 完整导出
│   ├── api.py                 # HTTP 接口
│   └── prompts/               # 运行时用的 prompt 文本（从 docs/prompts 复制）
│   # 第二档以后再加：surfacing.py diary.py decay.py keepsakes.py
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
  2. handoff：估算本窗 token，超过阈值就先走「交接」，开新窗口
  3. context：拼上下文
       角色 prompt（含世界书）
       + 语感笔记（最近 K 条）
       + 交接摘要（如果本窗口是接续的）
       + 本窗口的原话
  4. llm：生成回复
  5. gatekeeper（config 打开时）：自查
       像他 → 原样发出
       不像 → 用改写版发出，存一条语感笔记
  6. store：存 AI 回复
  7. 返回回复给前端
  │
  ▼（回复发出后，后台执行，不阻塞玩家）
inflow：把这一轮（玩家原话 + 回复）总结成一条记忆，标情绪和确定度
```

## 交接的流程

```
本窗口 token 估算 ≥ 上限 × handoff.trigger_ratio
  1. 用 docs/prompts/handoff.md 让 AI 自己写交接：
     在做什么、聊到哪了、此刻的情绪
  2. store：存 handoff，关闭当前 session
  3. 交接里的情绪也写一条记忆（kind = handoff）
  4. 新建 session，记下它接续的 handoff
新窗口拼上下文时：交接摘要 + 上一窗最后 N 条原话
```

N 默认 20，可在 config 里改。

## 模块之间的依赖

```
api → chat → context → worldbook, store
           → llm
           → gatekeeper → llm, store, worldbook
           → handoff → llm, store
           → inflow → llm, store
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
| `llm.small_model` | 同 `llm.model` | 流入、守门人用的小模型，省钱 |
| `llm.context_limit` | 64000 | 模型上下文上限（token） |
| `character.prompt_file` | `worldbook/character.md` | 角色 prompt |
| `character.worldbook_dir` | `worldbook/` | 世界书目录，里面所有 .md 都读 |
| `handoff.trigger_ratio` | 0.75 | 用到上限的多少开始交接 |
| `handoff.raw_tail` | 20 | 新窗口带上一窗多少条原话 |
| `gatekeeper.pre_send` | true | 发出前是否自查 |
| `gatekeeper.voice_notes_in_context` | 10 | 拼上下文时带几条语感笔记 |
| `storage.db_path` | `data/yuwen.db` | |
| `server.host` / `server.port` | 127.0.0.1 / 8787 | |
