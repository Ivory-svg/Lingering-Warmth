# 给实作者的说明

你要写的是余温第一档（M1）的参考实现。动手之前按顺序读：

1. [docs/design.md](docs/design.md)：为什么这样做
2. [docs/architecture.md](docs/architecture.md)：模块、流程、目录、配置
3. [docs/schema.md](docs/schema.md)：表结构
4. [docs/interfaces.md](docs/interfaces.md)：HTTP 与模块接口、降级规则
5. [docs/prompts/](docs/prompts/)：运行时 prompt

文档和代码冲突时，以文档为准；觉得文档有问题，先停下来提出，不要自己改设计。

## 实作顺序

每一步做完、测试通过再做下一步。

1. 项目骨架：`pyproject.toml`、`config.example.toml`、`src/yuwen/config.py`
2. `store.py`：建表、迁移、各表的读写。写测试，用临时数据库
3. `llm.py`：OpenAI 兼容对话、token 粗估、重试。测试里用假的 HTTP 响应，不要真的调 API
4. `worldbook.py`、`context.py`：拼装顺序按 interfaces.md
5. `chat.py` + `api.py` 的 `POST /chat`：先不带守门人、流入、交接，跑通一轮
6. `inflow.py`：后台写记忆，含降级路径
7. `handoff.py`：触发、写交接、开新窗口、带入原话
8. `gatekeeper.py`：发出前自查、`POST /ooc/flag`、语感笔记进上下文，含降级路径
9. `export.py` + `GET /export`、`GET /health`
10. 最简网页聊天界面（单个 HTML 文件即可），能演示：聊天、点“他不会这么说”、看到交接分隔线

## 怎么算做完

- `pytest` 全部通过
- 用 `examples/worldbook.example.md` 和任意一个 OpenAI 兼容 API，能从网页聊天
- 把 `handoff.trigger_ratio` 调得很低，能看到交接发生，新窗口里他还接得上话
- 流入、守门人的模型返回乱码时，对话不中断（有测试覆盖）
- `GET /export` 导出的 JSON 能看到所有表

## 不要做的事

- 不要做成 MCP，记忆搭在后端里
- 不要引入 SQLite 以外的数据库，不要引入向量数据库
- 不要写入世界书文件
- 不要真删除 memories 里的行
- 不要实作第二、三档的功能，只按 schema.md 留好字段
- 不要往仓库里放任何真实的人的聊天记录、世界书、数据库
- 不要内置任何语音模型或原作素材
- 修改从 Ombre Brain 或 EbbingFlow 直接拿来的代码时，在文件头注明来源和改动，并同步更新 NOTICE

## 风格

- 注释和用户可见的文字用简体中文
- 只有 `store.py` 写 SQL，只有 `llm.py` 发网络请求
- 依赖越少越好：fastapi、uvicorn、httpx、pytest，第二档再加 numpy
