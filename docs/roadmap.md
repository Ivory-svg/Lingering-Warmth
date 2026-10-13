# 路线

## 项目路线

1. **开源模板仓库**（现在）：设计文档 + 第一档参考实现 + 校对
2. **收费服务**：搭建教学、角色 prompt 代写、OOC 调教、扉页整理
3. **托管 App**：有需求再做

前端的其他功能（一起看电影、听音乐、表情包等）和他的样子（声音、形象、报备）不放在这个仓库，单独开一个项目。余温这边只留来信事件的对外通知，让那边接得上。

## 参考实现的里程碑

名字跟着 [design.md](design.md)「名字对照」。做完的里程碑保留当时的叫法，方便对照代码。

### M1 · 第一档：他记得你

- [x] 配置读取、SQLite 建表与迁移
- [x] OpenAI 兼容客户端（对话 + token 粗估）
- [x] 世界书读取
- [x] 上下文拼装
- [x] `POST /chat` 跑通一轮对话
- [x] 流入：每轮后台写一条记忆
- [x] 交接：超阈值自动交接，新窗口带摘要 + 最后 N 条原话
- [x] OOC 守门人：发出前自查 + `POST /ooc/flag` + 语感笔记进上下文
- [x] `GET /export` 完整导出
- [x] 一个最简单的网页聊天界面，能演示以上功能

### M1.1 · 校对不出声

见 [design.md](design.md)「校对在界面上不出声」。

- [x] `POST /chat` 响应加上 `ooc`（原句、原因、语感笔记 id）
- [x] 新增 `GET /messages`，刷新后能还原对话和小标记
- [x] 网页：去掉每条回复下的“他不会这么说”按钮，改为长按 / 右键菜单
- [x] 网页：改写过或被标记过的回复，角落显示小标记，点开看原句和原因
- [x] 网页：没有 OOC 的回复不显示任何额外元素
- [x] 测试覆盖：改写时响应带 `ooc`、未改写时为 null、`/messages` 带回小标记
- [x] 迁移 002：`voice_notes` 加 `overturned_at`、`overturn_note`
- [x] 新增 `POST /voice-notes/{id}/overturn`：停用笔记、恢复原句（守门人改写的才恢复）
- [x] 拼上下文和 `GET /messages` 时，`meta.restored` 的消息改用语感笔记的 `original`
- [x] 网页：小标记点开后有“其实他会这么说”，可以留一句话，推翻后小标记消失、气泡换回原句
- [x] 测试覆盖：推翻守门人改写会恢复原句并停用笔记；推翻玩家标记只停用笔记；重复推翻不报错；`messages.content` 始终不变

### M1.2 · 扉页拆分与省钱

见 [design.md](design.md)「扉页拆成三份，按需带」「每说一句话要调几次模型」，接口见 [interfaces.md](interfaces.md) 的 worldbook、context、inflow、handoff、export。

- [x] 世界书读取改成 核心 / 词条 / 语料 三份；词条解析 `keys`、`priority`；保留旧格式兼容
- [x] 拼上下文：核心 + 命中词条 + 抽到的语料；守门人只看核心和命中词条
- [x] 配置：`character.name`、`character.player_name`、`worldbook.*`、`handoff.min_turns`、`inflow.every_n_turns`
- [x] 守门人、流入的 prompt 用 `character.name` 和 `character.player_name`，不再写死“这个角色”“玩家”
- [x] 交接：换窗后至少 `handoff.min_turns` 句才允许再换
- [x] 流入：支持每 N 轮总结一次；交接前没攒满的先总结掉
- [x] `GET /export?format=markdown`；网页导出给两个选项
- [x] README 加一张“每说一句调几次模型”的对照表
- [x] 测试覆盖：关键词命中 / 不命中、超出 entry_budget 时按 priority 取舍、语料抽样数量、旧格式兼容、换窗后 min_turns 内不会再交接、every_n_turns=3 时三轮才写一条记忆、Markdown 导出内容

### M2 · 第二档：他会想起来

#### M2.1 · 翻阅与褪墨

见 [design.md](design.md)「翻阅怎么找」「褪墨」，接口见 [interfaces.md](interfaces.md) 的 llm、embedder、surfacing。

- [x] 迁移 003：`memories` 加 `embedding_model`、`recall_count`
- [x] 配置：`embedding.*`、`surfacing.*`、`decay.*`；依赖加 numpy
- [x] llm：OpenAI 兼容的 embedding 请求；没配时视为关闭
- [x] embedder：写记忆后算向量；启动时补算缺失的和换了模型的
- [x] surfacing：候选过滤、余弦相似度、字面匹配退路、遗忘现算、排序取舍、更新 `last_recalled_at` 和 `recall_count`（M2.1.1 改成用上了才更新）
- [x] context：浮现的记忆插在交接摘要和交接原话之间
- [x] Markdown 导出里的记忆显示被想起几次
- [x] README：embedding 二选一（在线平台 / 不配），本地 Ollama 只在进阶里提一句，不写步骤
- [x] 测试覆盖：用假的 embedding（例如按关键词生成固定向量）测相似排序；字面匹配退路；封存的和眼前的不浮现；遗忘让旧记忆排后但不消失；被想起后 recall_count 增加、下次排名上升；embedding 失败时退回字面匹配；换模型后补算

#### M2.1.1 · 用上了才算想起

见 [design.md](design.md)「翻阅怎么找」，接口见 [interfaces.md](interfaces.md) 的 surfacing 第 6 步和 inflow，prompt 见 [prompts/inflow.md](prompts/inflow.md)。

现在只要递进去就算想起一次，不相关的记忆会越递越牢。改成他真的用上了才算。

- [x] chat：不再当场 `mark_recalled`；把这一轮最终递进上下文的记忆 id 写进回复的 `meta.surfaced_memory_ids`。发生交接的那一轮，只写交接后那一批
- [x] inflow：这批轮次里递过记忆时，prompt 加「递给他的往事」一段，解析 `used_memory_ids`；没递过就不加
- [x] inflow：只认这批里递过的 id；用上的 `recall_count + 1`，`last_recalled_at` 用递给它的那条回复的时间；同一批里同一条只加一次
- [x] 降级：字段缺失、格式不对、整个解析失败、走原话降级路径，都当没用上
- [x] `src/yuwen/prompts/inflow.md` 和文档里的 prompt 同步
- [x] 旧数据：没有 `surfaced_memory_ids` 的回复照常流入，不报错
- [x] 测试覆盖：递了没用不加次数；用了加次数且时间是那条回复的；交接那轮只记交接后那批；小模型返回没递过的 id 被忽略；`every_n_turns=3` 时三轮一起判断、同一条只加一次；解析失败不加次数；没递过记忆时 prompt 里没有那一段

#### 换名（接着做）

见 [design.md](design.md)「名字对照」。只换人看得到的中文，英文的模块名、函数名、配置项、HTTP 路径、表名都不动。

- [x] 网页上玩家看得到的字换成新名（例：导出选项、交接分隔线、小标记的说明、“其实他会这么说”附近的文字）
- [x] 代码里的中文注释、文件头说明、日志和错误提示换成新名
- [x] 念给模型的 prompt（`src/yuwen/prompts/`、`docs/prompts/`）**不换**：模型不认识“扉页”“随笔”，换了反而糊涂
- [x] `GET /export?format=markdown` 里玩家会读到的标题换成新名；JSON 的键不动
- [x] interfaces.md、architecture.md、schema.md 正文里的中文叫法跟着换（标题已换），英文标识符不动
- [x] 测试里断言中文文字的地方跟着改；测试全部通过

#### M2.2 · 登录与目录

见 [design.md](design.md)「目录」「登录：只有你能打开这本书」，接口见 [interfaces.md](interfaces.md)「HTTP 接口（登录与目录）」，表见 [schema.md](schema.md) 的 `auth_sessions`、`memory_history`。先做登录，再做目录：目录能改记忆，不能先于门锁上线。

**登录**

- [ ] 配置：`auth.password`、`auth.session_days`、`auth.secure_cookie`；`config.example.toml` 里 `password` 写占位文字
- [ ] 启动检查：密码为空、是占位文字、不到 8 位就不启动，报错说清楚去哪设
- [ ] 迁移 004：`auth_sessions`
- [ ] `auth.py`：密码比对（sha256 + `hmac.compare_digest`）、建会话、验会话（过期、指纹对不上就删）、`last_seen_at` 一小时最多写一次、按 IP 的输错限制
- [ ] 默认全锁的中间件：只放行 `GET /login`、`POST /login`、`GET /health`；页面跳 `/login`，接口回 401
- [ ] `POST`、`PATCH` 不是 JSON 的回 415
- [ ] `GET /login` 登录页、`POST /login`、`POST /logout`
- [ ] 网页：接口回 401 时跳到 `/login`；角落加“登出”
- [ ] `GET /export` 不导出 `auth_sessions`
- [ ] README「运行参考实现」加一步：在 `config.toml` 的 `[auth]` 里设密码；放在 https 后面的打开 `secure_cookie`
- [ ] 测试覆盖：没设密码不启动；对的密码能进、错的 401；第 6 次错 429 带 `Retry-After`；过期 cookie、伪造 cookie、换密码后的旧 cookie 都进不去；登出后进不去；**遍历 app 里所有路由，除了放行的三个，没登录一律进不去**（以后新加接口忘了也会被这条测出来）；非 JSON 的 POST 回 415；现有测试改成先登录再测

**目录**

- [ ] `catalog.py`：列、筛、搜、分页；还记得多少直接用 surfacing 里的同一个函数
- [ ] `GET /memories`、`GET /memories/{id}`、`PATCH /memories/{id}`、`POST /memories/{id}/seal`
- [ ] 改正和封页前先写 `memory_history`（不含 embedding）；改正后 `certainty = 1.0`、清空向量并后台重算
- [ ] `catalog.html`：一条一行，最新在上；字的颜色跟着还记得多少变淡；顶上四个筛选和搜索；点开看原话、改、封页 / 撕开；改的时候提示“用名字称呼两个人”
- [ ] 聊天页顶上加“目录”入口
- [ ] 测试覆盖：四种筛选各自对；搜索命中 summary、quote、emotion_text；梦不进目录；`retention` 和翻阅算出来的一样；改正写历史、`certainty` 变 1.0、向量被清空；改成一样的不写历史；超长 422；封页后翻阅找不到、撕开后又能找到；封页和撕开都写历史；重复封页不报错；没有任何删除

#### M2.3 · 之后

- [ ] 封页：对话里说“这个忘掉吧”就封（目录里的手动封页在 M2.2）
- [ ] 折角：工具 + 日历页面 + 折角回复
- [ ] 夹页
- [ ] 指痕
- [ ] 待续：memories 加 status 等字段；写随笔时识别新的承诺 / 待办；对话里或玩家一句话就能完结；翻到时带上“这是哪天的状态”

### M3 · 第三档：他会想你

- [ ] 修订：改写记忆，保留历史；校对读修订
- [ ] 留白：定时任务；写进按月的梦的合集，不单独开条目，不参与普通翻阅，和上一个梦几乎一样的不记
- [ ] 来信：定时看一眼，自己决定写不写；写了就对外通知（webhook），给声音、形象、报备那个项目用
- [ ] 纪年

## 还没想清楚的

- [ ] 续写带多少条原话：先默认 20，等实际用起来再调
- [ ] 情绪怎么标：参考实现先文字 + 数值都存，以后看哪个没用上再删
- [ ] 来信多久看一次：给别人的默认值，以及要不要让玩家自己调
- [ ] 本仓库自己用什么协议发布
- [x] 放到服务器上给别人用之前，要先加登录：排进了 M2.2
