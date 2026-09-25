# 知我 · PLAN

> V1.0 启动基线｜更新日期：2026-09-25  
> 本文是唯一进度入口。范围见 [PRODUCT.md](PRODUCT.md)，实现约束见 [ARCHITECTURE.md](ARCHITECTURE.md)，执行规则见 [AGENTS.md](AGENTS.md)。

## 1. 当前状态

| 项目 | 状态 |
| --- | --- |
| 已有输入 | 产品方案、四份启动文档、P0.1 原生 Windows 存取证据 |
| 工程完成情况 | P0 验收结论为 GO。P1.1 至 P1.5 已通过 |
| 当前阶段 | **P1 · 核心闭环，已通过** |
| 当前任务 | **P2.1 · 连接与授权** |
| 下一阶段 | P2；P1.5 已通过 |
| 当前风险 | 改写查询 `r07` 未进前五。检索会带回目标以外的记忆。模型首次下载需手工缓存。A03 的 MCP 查询和 A11 的删除恢复尚未验 |

P0.1 使用 uv 管理的 CPython 3.12.13。本机自带 Python 3.9.10 不满足 `mnemosyne-memory` 的 `>=3.10` 要求。

任务状态统一为：`TODO / DOING / BLOCKED / DONE`。没有实际证据不得标 `DONE`；缺 Windows 环境时记录 `NOT_RUN`，不能用 Linux 或 WSL 测试代替原生 Windows 验收。

## 2. 开发顺序

| 阶段 | 要回答的问题 | 可检查交付物 | 当前状态 |
| --- | --- | --- | --- |
| P0 内核验证 | 选定 Kernel 能否守住产品边界？ | 锁定版本、最小脚本、中文样本、实测结果、继续/阻塞结论 | DONE |
| P1 核心闭环 | 用户能否导入、审核、看到可纠正的记忆？ | 本地 Web + 真实双库 + 审核/版本/画像 | DONE |
| P2 真实接入 | Agent 能否按授权取得记忆并提出变更？ | 一个真实 MCP 客户端、四个工具、授权与访问记录 | TODO |
| P3 产品化 | 普通目标用户能否在 Windows 上完成整个流程？ | 四页界面、Electron 安装包、备份恢复与异常处理 | TODO |

P0 不开始完整 UI 和安装包；P1 不打磨动效；P2 不扩展多个客户端兼容矩阵。每阶段先跑通一个真实纵向流程，再补本阶段边界。

## 3. P0 · Kernel Spike

**目标：证明 Mnemosyne 能作为唯一正式记忆库；不能证明就停止扩建。**

- [x] **P0.1** 在 Windows 原生 Python 环境安装选定版本；记录 Python/Node/包管理器/Kernel 版本及 tag/commit，固定依赖；使用独立目录写入一条中文记忆，重启后读回。
- [x] **P0.2** 验证事实/事件映射、当前版本/历史、更新/删除、有效期；逐项识别自动提取、晋升、合并、压缩及清理行为，并证明可关闭或隔离。验证 `operation_id` 可查证，失败重试不会重复写入。补验已通过。向量索引删除在 P0.3 实测。
- [x] **P0.3** 验证中文本地召回：固定 20 条测试记忆和 20 个正向查询；准备模型后断网复测。直接表达 10/10，改写 9/10，合计 19/20。无关查询未返回记忆；过期和已取代项不进入 `recall()`，`get()` 仍能读回。删除后正文、派生表和向量索引无目标残留。
- [x] **P0.4** 用已安装的 OpenCode 1.18.16 完成 stdio 握手、工具发现和 `search_memory` 实际调用。正确测试凭证返回合成库中的目标记忆；错误凭证只得到 `UNAUTHENTICATED`，没有正文。重启后仍可连接。凭证不在服务日志中。
- [x] **P0.5** 在本文件记录能力矩阵、实际命令、结果和问题；脚本保存在 `experiments/kernel_spike/`。验收结论为 **GO**。依赖、中文模型、版本级 session 和限定范围的派生表清理已冻结。P0 实验不再扩展。

**P0 通过条件：**

1. Windows 原生安装、持久化、重启读回均成功。
2. 已确认记忆不会被后台流程擅自改写、过期清除或合并成不可追溯正文；未审核或自动派生内容可完全隔离。
3. 保留旧版本、精确版本读取、完整删除、操作查证有可用接口或明确的 Adapter 映射；不依赖另一份正式事实库。
4. 20 个正向查询中至少 16 个在前 5 条命中预先标注的目标记忆，即 `Hit@5 ≥ 80%`。直接表达与改写各 10 个，逐例报告；这是 MVP 自建样本门槛，不是行业指标。
5. 真实 stdio 客户端通信成功。核心模型预先准备后，本地读取/召回不依赖外部网络。

先测试一套中文本地模型配置，失败可再试一套兼容配置；仍不通过就汇报。不得转成无限参数扫描、重写检索引擎或替换底座。P0 不承诺性能指标，不运行大型公开 benchmark。

## 4. P1 · 核心闭环

**前置：P0 通过。先用本地 Web 验证产品数据链路。**

- [x] **P1.1** 建立服务、业务库迁移、contracts 与 Adapter；独立测试数据目录、健康状态、Owner 认证。服务可启动和重启。重复迁移不重复应用已有版本。错误 Owner 凭证得到 `UNAUTHENTICATED`。Adapter 连到指定目录。实验数据未改。
- [x] **P1.2** 实现手动保存、来源存储、粘贴/TXT/Markdown 导入、提取任务及候选；未配置云模型时保留手动入口。手动保存走统一发布入口。同一请求只产生一条正式记忆。候选不进入正式检索。云模型提取标 `NOT_RUN`。
- [x] **P1.3** 实现审核、拒绝、更新、两者保留、版本冲突、幂等与失败恢复；正式发布前不对读取开放。云模型提取仍为 `NOT_RUN`。
- [x] **P1.4** 完成“关于我 / 记忆 / 待确认”的基础界面；真实显示画像、来源、版本及共享开关；“我的 Agent”可暂为空态。
- [x] **P1.5** 通过 A01–A05 的 Owner 界面/服务部分、A11 的发布与重试部分、A12；演示拒绝提案后画像和正式检索均未被污染。MCP 及删除恢复部分在 P2/P3 补验。

**交付：** 一条真实中文导入 → 审核 → 正式记忆 → 画像 → 修改并追溯版本的闭环；数据重启后仍在。仅有静态卡片或 mock 数据不通过。

## 5. P2 · 真实 MCP 接入

**前置：P1 通过。首版只验收一个真实客户端。**

- [ ] **P2.1** 实现连接创建、专用凭证、类别/工具授权、停用及重置；Agent 不能访问 Owner API。
- [ ] **P2.2** 实现四个 MCP 工具与统一 PolicyEngine；任何读取，包括解释入口，都执行相同可见性检查。
- [ ] **P2.3** 实现访问快照与“我的 Agent”页面，核对实际返回载荷；审核记录与访问记录分开。
- [ ] **P2.4** 在真实客户端完成查询、提案、用户审核、再次查询；通过 A06–A10，并重跑 A03 的拒绝隔离。

**交付：** 能展示“这个 Agent 本次得到这几条信息，其他类别没有提供”，并能证明停用后下一次调用被拒绝。无需第二个客户端才能验收。

## 6. P3 · 桌面产品化

**前置：P2 通过。**

- [ ] **P3.1** 统一四页、详情抽屉、Diff、空态、加载、错误、键盘焦点与基本视觉；检查 1024 px 和常见桌面宽度。
- [ ] **P3.2** Electron 管理 Python 服务；打出 Windows 安装包，在无开发环境的测试用户/机器上安装验证。明确首次模型准备的网络与磁盘需求。
- [ ] **P3.3** 完成模型设置、可读导出、配套备份/恢复、永久删除与清空；恢复后不复活旧 Agent 凭证。
- [ ] **P3.4** 完成 A01–A14 的最终验收，保存关键截图与命令结果；记录已知限制，进行一次目标用户体验走查。

**交付：** Windows 可运行安装包 + 5 分钟真实演示 + 验收记录。发布前验收中的权限/审核泄漏为零；不能通过修改样本或删除失败场景获得通过。

## 7. 验收场景

所有测试使用合成数据。P0 冻结数据和预期后，修改用例需注明理由；不得为掩盖当前失败调整标准。

| ID | 场景与动作 | 通过标准 | 首次验收 |
| --- | --- | --- | --- |
| A01 | 手动添加“日常回复偏好简洁”，再编辑保存 | 保存即生效，无多余审核；重启可读，旧版本可查 | P1 |
| A02 | 粘贴/TXT/Markdown 分别导入；提取后只批准部分候选 | 来源可追溯；只有批准项进入正式库和画像；重复请求不重复导入 | P1 |
| A03 | 待审核或已拒绝的候选含独特测试词；用列表、搜索、画像与 MCP 查询 | 各入口均无候选正文；提案只能在 Owner 待确认/记录中看到 | P1/P2 |
| A04 | 更新偏好；另一提案仍引用旧版本；随后按不同场景保留两条 | 更新只产生一个当前版本；旧提案报冲突；共存项场景明确 | P1 |
| A05 | 首页展示已确认事实，点击每条查看依据并纠正 | 每句均有记忆 ID/版本来源；无推断补写；纠正后刷新画像 | P1 |
| A06 | 固定中文直接/改写查询及无关查询 | 正向 `Hit@5 ≥ 80%`；无关任务不返回整份画像；记录漏召回案例 | P0/P2 |
| A07 | Agent A 仅获目标类权限，B 无权限；伪造身份、跨类搜索、直调 Owner API | A 只得目标类；B、伪造身份和越权调用被拒；无正文或来源泄漏 | P2 |
| A08 | 停用 Agent、停止共享、设置过期；覆盖检索进行中的撤销 | 撤销完成后提交的新响应不含受限内容；历史/过期不作为当前返回 | P2 |
| A09 | 解释获准记忆；来源同页含其他类别；解释未获准 ID | 只返回获准的已审核片段；不泄露整份来源、旧版本及 ID 是否存在 | P2 |
| A10 | 比较 MCP 序列化响应与记录；模拟日志写入失败、发送中断 | 快照与响应一致；日志失败不发送记忆；中断不误标“已收到/已采用” | P2 |
| A11 | 重复提交审核；Kernel 成功后模拟业务提交失败并重启；删除途中中断 | 无重复正式记忆；未提交版本不曝光；可恢复到一致状态，删除可继续 | P1/P3 |
| A12 | 模型已准备后断网；无 API Key；提取超时/返回无效结构 | 本地保存、读取、检索可用；云提取明确失败，保留来源；无静默云端兜底 | P1 |
| A13 | 导出、备份、恢复、单条永久删除及清空；检查凭证和内容副本 | 导出可读且无密钥；恢复内容/关联一致且 Agent 全停用；应用管理的已删内容不再出现 | P3 |
| A14 | Windows 安装、启动、关闭/重启；一个真实客户端走完整流程 | 不需开发工具；无残留写入进程或假连接状态；真实日志可查；无 mock 代替 | P3 |

**建议演示脚本（全部为虚构测试人物）：**

导入“林舟做产品运营，最近准备转 AI 产品，平时希望回复简短，正式报告需要详细论据”，再加入一条无关私人信息。审核后查看画像，只授权“目标/偏好”给 Agent；Agent 查询后提议“所有回答都越短越好”；用户拒绝，再次查询应仍保留原场景偏好；最后停用连接验证拒绝访问。

## 8. 当前任务卡：P2.1

P0 验收结论为 **GO**。P1.5 已通过，证据见 `tests/results/p1_5_windows.json` 和 `tests/results/p1_5_extract.json`。下一步是 P2.1。本轮没有做 Agent 连接、删除、设置页或 Electron。

### P1.5 结果

直接编辑已确认记忆走 `PATCH /api/v1/memories/{id}`，进入 `publish_memory()`，不创建提案。更新载荷包含 `source_refs`。真实提取使用 DeepSeek `deepseek-flash`，`ZHIWO_TEST_MODE=0`。超时和无效格式来自本机测试端点，与真实提取分开记录。

| 检查 | 结果 |
| --- | --- |
| A01、A05 直接编辑 | 添加“日常回复偏好简洁”后，在详情里改为“日常回复偏好先给结论”并勾选“仅自己可见”。页面说明这次修改直接生效、没有进入待确认。版本 1 为历史，版本 2 为当前。画像同步为新正文。重启后仍是版本 2。截图 `tests/results/p1_5/p1_5_direct_edit.png`、`p1_5_restart.png`、`p1_5_profile_after_review.png` |
| A02 粘贴、TXT、Markdown | 粘贴在页面完成。TXT `notes.txt` 与 Markdown `plan.md` 走页面相同的 `POST /api/v1/imports`。只批准部分候选。批准项带来源。同一 `Idempotency-Key` 再提交不增加来源或候选。样本见 `tests/results/p1_5_extract.json` |
| A03 未批准内容 | 待确认“最近准备转 AI 产品”和已拒绝的“正式报告需要详细论据”等正文，不在记忆列表和画像中。搜索若有结果，也是其他已确认记忆，不含这些正文。MCP 查询 `NOT_RUN`，留到 P2 |
| A04、A11 | 沿用 `tests/results/p1_3_windows.json`：`update_conflict`、`keep_both`、审核重放，以及 kernel/commit 两处中断恢复均为通过。本轮没有重跑。删除中断仍是 P3，`NOT_RUN` |
| 真实提取 | 服务商 DeepSeek，模型 `deepseek-flash`。原文、四条候选和证据见 `tests/results/p1_5_extract.json`。人工核对后，“日常回复希望简洁”没有被写成“所有场景都简洁”。批准身份和这条偏好，拒绝“正式报告需要详细论据”，目标留在待确认。画像只有已批准内容 |
| A12 未配置 | 来源保留，状态 `extractor_unavailable`，提案 0 条。页面写明提取模型未配置。截图 `p1_5_import_unconfigured.png` |
| A12 超时 | 本机测试端点，不是真实模型。`error_code=TIMEOUT`。页面写明提取超时、来源仍在、可以重试。重试后仍是一条来源、零条提案。截图 `p1_5_timeout.png` |
| A12 无效格式 | 本机测试端点，不是真实模型。`error_code=VALIDATION_ERROR`。页面写明格式无效、来源仍在、可以重试。重试后仍是一条来源、零条提案。截图 `p1_5_invalid.png` |
| A12 断网 | 进程的 `HTTPS_PROXY` 指向 `127.0.0.1:9`，对外请求没有 HTTP 响应。本地仍能保存、读取并搜索“断网探针：书桌靠窗”。同一进程里的云提取返回 `MODEL_UNAVAILABLE`，来源保留，正式检索没有该正文。截图 `p1_5_offline_search.png`、`p1_5_offline_extract.png` |

截图在 `tests/results/p1_5/`。复核入口：`services/publish.py` 的 `publish_memory()`，`services/review.py` 的 `decide_proposal()`，`services/extract.py` 的 `extract_candidates()`，`services/memories.py` 的 `update_memory()`，`apps/web/src/Detail.tsx`。

### P1.4 结果

页面在 `apps/web/`，通过本机 Owner API 读写。画像只列出已确认且当前有效的事实，按身份、目标、偏好、项目分组；点击卡片查看来源和版本。正式正文仍只在 Kernel。来源按文本显示。

正常导入只调用 `POST /api/v1/imports`。未配置提取模型时，页面说明来源已保存、没有待确认内容。演示候选只在服务显式处于测试模式时出现，页面标明“演示数据”。

| 检查 | 结果 |
| --- | --- |
| 手动添加 | 保存后出现在“关于我”的偏好和记忆列表。勾选“仅自己可见”后，卡片显示该状态 |
| 搜索 | 搜索“结论”只返回这条已确认记忆 |
| 未配置模型的导入 | 页面写明提取模型未配置、来源已保存、没有生成待确认内容。待确认仍是 0 条 |
| 演示审核 | 测试模式横幅和候选都标明演示数据。更新后版本 1 为历史、版本 2 为当前。来源里的脚本标签按原文显示 |
| 版本冲突 | 页面提示当前版本已经变化，没有覆盖最新内容。建议仍待确认，正式正文保持冲突前的版本 3 |
| 刷新与重启 | 重新加载和重启服务后，版本 3 与待确认候选都还在 |
| 服务停止 | 顶栏显示本地服务未运行。保存失败时草稿仍留在输入框，并说明可以重试 |
| 我的 Agent | 未连接空态，没有创建或授权入口 |
| 云模型提取 | `NOT_RUN` |

截图在 `tests/results/p1_4/`。

### P1.3 结果

审核继续调用 `publish_memory()`。`POST /api/v1/proposals/{id}/decision` 接受 `accept`、`update`、`keep_both`、`edit` 和 `reject`。再次提交时比较已保存的审核意图。决定、编辑内容、场景、目标记忆和目标版本都相同，就返回第一次的结果，即使 `Idempotency-Key` 不同。意图不同则返回 `409 CONFLICT`，说明实际状态，正式记忆不变。拒绝不写 Kernel，也不改当前版本。

更新写入新的 Kernel 版本。控制库在同一事务里把新版本标为 `active`、旧版本标为 `superseded`。请求带 `base_revision`；当前版本已经变化时返回 `CONFLICT`，提案保持 `pending`。两者保留必须带明确 `scope`，新建 `memory_id`，原记忆仍有效。`GET /api/v1/memories/{id}/versions` 从 Kernel 读回历史正文。

恢复不另建调度。操作记录保存原始载荷。重试时先核对载荷和版本 session 里已有的 Kernel 行，符合才补完控制库。未写入 `memory_refs` 的 Kernel 行不进入正式检索。`ZHIWO_CRASH_AFTER=kernel|commit` 只在 `ZHIWO_TEST_MODE=1` 且数据目录位于系统临时目录时退出进程。

| 检查 | 结果 |
| --- | --- |
| 审核通过只发布一次 | 相同决定和载荷、换了 Key，返回原来的记忆与操作。Kernel 中该正文 1 行 |
| 通过后拒绝 | `409 CONFLICT`，说明已经通过。正式记忆不变 |
| 拒绝 | 正式检索没有该正文。Kernel 中 0 行。原记忆的版本列表不变 |
| 拒绝后通过 | `409 CONFLICT`，说明已经拒绝。正式记忆不变 |
| 同决定但修改载荷 | `409 CONFLICT`。原正文仍在，改后的正文不在 Kernel |
| 更新与冲突 | 两个请求同时基于版本 1。一个成为版本 2，另一个 `CONFLICT` 且提案仍为 `pending`。旧正文可在版本列表读回，正式检索只看到新正文 |
| 两者保留 | 缺少场景时 `VALIDATION_ERROR`，提案仍待审核。补上“正式报告”后出现另一条记忆，两条都可单独检索和查看版本 |
| 编辑后保存 | 发布的是改后的正文。候选原文不在 Kernel，也不在正式检索 |
| Kernel 已写、控制库未提交 | 进程退出。重启后的检索看不到该正文；操作仍是 `prepared`。用另一个 Key 重试后发布一次，Kernel 仍是 1 行 |
| 控制库已提交、响应未返回 | 进程退出。重启后检索已经能看到。重试返回原来的操作，Kernel 仍是 1 行 |
| 云模型提取 | `NOT_RUN`。候选仍来自本机 HTTP 测试端点 |
| 实验数据 | 文件指纹未变 |

验收命令：`server\.venv\Scripts\python.exe tests\test_p1_3_service.py`。P1.2 在这次改动后复跑仍通过。

### P1.2 结果

正式写入只有 `publish_memory()`。手动保存调用它；导入和提取不调用它。有效的 `memory_refs` 必须有 `kernel_id`，同一记忆的 `lifecycle='active'` 最多一行。Kernel 行和本地向量都核对之后，才把操作标成 `completed`。

`ZHIWO_KERNEL_CONNECT_ONLY=1` 才关闭向量，并且只用于连接测试。正式写入在进程启动前设置 `BAAI/bge-small-zh-v1.5`，缓存目录用 `ZHIWO_FASTEMBED_CACHE_DIR`。模型没就绪时保存返回 `MODEL_UNAVAILABLE`，不登记有效版本。

| 检查 | 结果 |
| --- | --- |
| 手动保存一条合成中文记忆 | 重启后的新进程能检索到，`kind=fact`，`category=preference` |
| 同一 `Idempotency-Key` 再提交 | 记忆 id 与 kernel id 不变；Kernel 里该正文只有 1 行；不同正文返回 `CONFLICT` |
| 粘贴、TXT、Markdown | 来源保留。本地测试端点生成 3 条带证据的 `pending` 候选 |
| 候选是否进入正式检索 | 检索候选正文得到 0 条。Kernel 里没有候选正文 |
| 无提取模型 | 手动保存仍成功。来源状态为 `extractor_unavailable`，提案 0 条 |
| 提取返回无效格式 | 来源仍在，状态 `failed`，重试后仍是 `failed`，没有提案 |
| 超过 1 MiB、非法 UTF-8 | 均被拒绝，没有写入来源 |
| 关闭向量的连接测试 | 保存返回 503，`memory_refs` 为 0 |
| 云模型提取 | `NOT_RUN`。候选来自本机 HTTP 测试端点，不是云模型 |
| 实验数据 | 文件指纹未变 |

验收命令：`server\.venv\Scripts\python.exe tests\test_p1_2_service.py`。

### P1.1 记录

P1.1 已通过，证据见 `tests/results/p1_1_windows.json`。schema 现为 4；连接测试仍用 `ZHIWO_KERNEL_CONNECT_ONLY=1`。复跑进程 27108 与 22228。

### P1.1 结果

正式服务在 `server/`，不导入 `experiments/`，也不读实验 `control.json`。`GET /health` 只返回进程状态。`GET /api/v1/health` 要求 Owner 凭证。控制库是 `ZHIWO_DATA_DIR/zhiwo.db`，Kernel 库是同一目录下的 `kernel/mnemosyne.db`。

| 检查 | 结果 |
| --- | --- |
| 启动 / 重启 | 复跑进程 32796 与 14208，公开健康检查均为 200 |
| 迁移 | 两次启动后 `schema_migrations` 为 4 行，版本 4。版本 4 保存审核意图 |
| 错误 Owner 凭证 | 401，`UNAUTHENTICATED`，响应里没有 Kernel 路径 |
| 缺少凭证 | 401 |
| Adapter | `mnemosyne-memory` 3.15.1，数据库在指定数据目录内 |
| 实验数据与本机 Hermes 库 | 文件指纹未变 |
| 服务日志 | 不含 Owner 凭证 |

`memory_refs` 没有正文字段。`kind` 只允许 `fact/event`，`category` 只允许 `identity/goal/preference/project/event/other`，适用场景写在 `scope`。实验标签 `unspecified` 和把场景写入 category 会被拒绝。P1.2 之后，只有 `ZHIWO_KERNEL_CONNECT_ONLY=1` 才设置 `MNEMOSYNE_NO_EMBEDDINGS=1`。当时的 P1.1 证据仍是连接测试，不加载向量模型。

启动命令：

```powershell
cd server
$env:ZHIWO_DATA_DIR = "独立数据目录"
$env:ZHIWO_OWNER_CREDENTIAL = "本机 Owner 凭证"
.\.venv\Scripts\python.exe -m uvicorn zhiwo.api.app:app --host 127.0.0.1 --port 8765
```

凭证只放在环境变量里，不写入 `zhiwo.db`。

### 已冻结的 P0 选型

P0.1–P0.4 的实验没有重跑。过滤缺陷的证据见 `experiments/kernel_spike/results/p0_5_filter.json`。P0 实验不再扩展。

### 固定版本

| 项 | 版本 |
| --- | --- |
| 系统 | Windows 10.0.26200，AMD64 |
| CPython | 3.12.13，`uv lock` 的 `requires-python` 为 `==3.12.13` |
| uv | 0.11.17 |
| Node / pnpm | v24.13.0 / 11.21.0 |
| Kernel | `mnemosyne-memory==3.15.1`，tag `v3.15.1`，commit `78506708aae344635e01a24f67a7319efc36fce9` |
| 本地向量 | `fastembed==0.8.1`，`sqlite-vec==0.1.9`，模型 `BAAI/bge-small-zh-v1.5` |
| MCP SDK | `mcp==2.2.0`。这是知我实验桥的 SDK，不是 Mnemosyne 原生工具服务 |
| 真实客户端 | OpenCode 1.18.16，模型 `opencode-go/deepseek-v4-flash` |

实验依赖锁仍是 `experiments/kernel_spike/uv.lock`，没有改动，也没有再扩客户端。正式服务锁是 `server/uv.lock`：Kernel 与向量包版本与实验锁相同，另固定 `fastapi==0.141.1`、`uvicorn==0.53.0`。MCP SDK 仍只属于实验桥。

### 阶段结果

| 阶段 | 结论 | 证据 |
| --- | --- | --- |
| P0.1 | 原生 Windows 写入一条中文记忆，新进程读回 | `results/p0_1_windows.json` |
| P0.2 | 版本 session、同一库跨 session 召回、操作冲突、派生表清理重试、单 session 上限拒绝均通过 | `results/p0_2_windows.json`，`p0_2_followup.json`，`p0_2_session.json` |
| P0.3 | 直接 10/10，改写 9/10，合计 19/20。断网复测相同。过期和已取代项不进 `recall()`。删除后正文、派生表和向量索引无目标残留 | `results/p0_3_windows.json` |
| P0.4 | OpenCode 完成握手、工具发现和两次 `search_memory`。目标 m06 在工具结果里。错误凭证为 `UNAUTHENTICATED`。重启后的进程号不同 | `results/p0_4_windows.json` |
| P0.5 过滤修复 | 未登记行不占用 `limit`；两条合格且 `limit=1` 时返回一条并 `truncated=true` | `results/p0_5_filter.json` |

实际命令都在 `experiments/kernel_spike/`：`p0_1_roundtrip.py`、`p0_2_boundaries.py`、`p0_2_followup.py`、`p0_2_session.py`、`p0_3_recall.py`、`p0_4_run.py`，以及 `python -m unittest test_search_filter.py`。

### 清理例外

仅 `mnemosyne-memory==3.15.1`。`forget()` 之后，`cleanup_derived_rows()` 在同一事务里删除 `gists.memory_id` 与 `memoria_facts.source_memory_id` 等于目标 kernel id 的行。正文已经不在时，重试仍要清理。共用行失败并回滚。不按正文模糊匹配，不清空整表，不改 schema。向量索引由 Kernel 自己的 `forget()` 删除，不扩大这个函数。

### 实验不能当成正式实现

控制记录和 Kernel 正文的字符串比对只用于实验断言。`kind="unspecified"`、用场景充当 category、没有发布状态、没有权限检查，都不能进入 P1/P2。正式读取仍按已发布版本、类别、共享、有效期和精确版本过滤。P0.5 不建设业务库、PolicyEngine、另外三个 MCP 工具或完整 Gateway。

实验召回先取 40 条候选，再按合格结果套用 1–20 的返回上限。40 不是全库扫描，也不是产品里的权限批次策略。

### 已知限制

- 改写查询 `r07` 的向量近邻没有通过工作记忆的字面门槛，公开 `recall()` 未进前五。直接表达 10/10，改写 9/10。
- d06 的工具结果除了目标 m06，还返回了 m12、m05、m19。P0.3 另有一条无关查询返回 0 条；这两件事都保留，不把其中一次写成全部查询的表现。
- `BAAI/bge-small-zh-v1.5` 首次从 Hugging Face 下载时 TLS 中断。随后手工放入 Qdrant GCS 压缩包 `fast-bge-small-zh-v1.5.tar.gz`（54584282 字节）。安装体验留到产品化。
- 条数上限只约束单个 Kernel session 里尚未巩固的行，不是整个记忆库的容量。
- 只验证了 OpenCode 一个真实客户端。Cursor 3.21.18 没有可脚本化的无头工具调用。Claude Code 2.1.158 能发现工具，但模型请求返回 HTTP 400，调用没有执行。

### 结论

P0 验收结论为 **GO**。上述限制保留，不在 P1.1 里重跑或扩实验。

## 9. 进度与决策记录

每完成一个任务，在此追加一行，并同步阶段、当前任务和勾选状态。结果文件可放实验或测试目录，无需另写第五份项目说明文档。

| 日期 | 任务 | 状态 | 结果 / 证据路径 | 剩余问题 |
| --- | --- | --- | --- | --- |
| 2026-09-25 | 启动文档整理 | DONE | 根目录四份 Markdown；仅文档交付 | 工程阶段尚未验收 |
| 2026-09-24 | P0.1 | DONE | `experiments/kernel_spike/results/p0_1_windows.json`；锁文件 `experiments/kernel_spike/uv.lock` | 只验证主键读回。`.python-version` 已补为 `3.12.13` |
| 2026-09-24 | P0.2 | DONE | `experiments/kernel_spike/results/p0_2_windows.json`；`p0_2_followup.json`；`p0_2_session.json` | 补验通过。向量索引删除改在 P0.3 |
| 2026-09-24 | P0.3 | DONE | `experiments/kernel_spike/results/p0_3_windows.json`；样本 `experiments/kernel_spike/fixtures/p0_3_queries.json` | 直接 10/10，改写 9/10。失败例 `r07`。断网复测同为 19/20。首次从 Hugging Face 下载 `BAAI/bge-small-zh-v1.5` 时 TLS 中断，随后用 Qdrant GCS 压缩包手工放入缓存。安装体验留到产品化 |
| 2026-09-24 | P0.4 | DONE | `experiments/kernel_spike/results/p0_4_windows.json` | OpenCode 1.18.16，模型 `opencode-go/deepseek-v4-flash`。握手、工具发现和两次 `search_memory` 都返回 m06。错误凭证为 `UNAUTHENTICATED`。整个 P0 未通过 |
| 2026-09-25 | P0.5 | DONE | `experiments/kernel_spike/results/p0_5_filter.json`；本节能力矩阵 | 过滤按合格结果计数。未重跑 P0.1–P0.4。验收结论 GO |
| 2026-09-25 | P1.1 | DONE | `tests/results/p1_1_windows.json` | 服务可启动和重启。重复迁移、错误 Owner 凭证和独立目录连接已验证。连接测试不加载向量模型 |
| 2026-09-25 | P1.2 | DONE | `tests/results/p1_2_windows.json` | 手动保存、幂等、来源和候选已验证。云模型提取 `NOT_RUN`。下一步是 P1.3 |
| 2026-09-25 | P1.3 | DONE | `tests/results/p1_3_windows.json` | 审核、版本冲突和两处中断恢复已验证。相同意图才重放；不同意图返回冲突。云模型提取仍 `NOT_RUN` |
| 2026-09-25 | P1.4 | DONE | `tests/results/p1_4_windows.json`；截图 `tests/results/p1_4/` | 三个页面连到真实服务。云模型提取仍 `NOT_RUN`。下一步是 P1.5 |
| 2026-09-25 | P1.5 | DONE | `tests/results/p1_5_windows.json`；`tests/results/p1_5_extract.json`；截图 `tests/results/p1_5/` | 直接编辑、部分批准和 DeepSeek `deepseek-flash` 提取已验证。A03 的 MCP、A11 的删除恢复未跑。下一步是 P2.1 |

待验证能力记录：

| 能力 | 状态 | 证据 / 阻塞 |
| --- | --- | --- |
| 精确依赖 + Windows 原生存取 | DONE | Windows 10.0.26200；CPython 3.12.13（`.python-version` 与 `uv.lock` 均为 `==3.12.13`）；`mnemosyne-memory==3.15.1`。见 `experiments/kernel_spike/results/p0_1_windows.json` |
| 自动整理隔离、历史/删除、幂等定位 | DONE | 版本 session 与派生表清理见 `experiments/kernel_spike/results/p0_2_session.json`。条数上限是单个 session 内未巩固行，不是全库容量。向量索引删除见 `p0_3_windows.json` |
| 中文本地召回 + 断网 | DONE | 模型 `BAAI/bge-small-zh-v1.5`，`fastembed==0.8.1`，`sqlite-vec==0.1.9`。统一 `recall()` Hit@5 为 19/20。见 `experiments/kernel_spike/results/p0_3_windows.json` |
| 一个真实客户端 + stdio 凭证链路 | DONE | OpenCode 1.18.16。服务端 `mcp==2.2.0`，只注册 `search_memory`。凭证经环境变量传入，库存 sha256。见 `experiments/kernel_spike/results/p0_4_windows.json` |
| 本机服务、控制库、Owner 认证、Adapter 连接 | DONE | `tests/results/p1_1_windows.json`。连接测试不加载向量模型 |
| 手动保存、来源、导入候选 | DONE | `tests/results/p1_2_windows.json`。云模型提取 `NOT_RUN` |
| 审核、版本冲突、失败恢复 | DONE | `tests/results/p1_3_windows.json`。云模型提取仍 `NOT_RUN` |
| 关于我、记忆、待确认 | DONE | `tests/results/p1_4_windows.json`。云模型提取仍 `NOT_RUN` |
| Owner 验收与真实提取 | DONE | `tests/results/p1_5_windows.json`；`tests/results/p1_5_extract.json`。DeepSeek `deepseek-flash`。MCP 与删除恢复未跑 |

范围或架构变更先记录：**问题证据 → 最小可行选项 → 推荐方案 → 对范围/数据/计划的影响 → 用户决定**。决策通过后再同步相关文档，不在实现中悄悄改变基线。

P0.2 决定（2026-09-24，实施前记录）：

- **派生表清理：批准。** Adapter 可在 `forget()` 后删除 `gists.memory_id` 与 `memoria_facts.source_memory_id` 等于目标 kernel id 的行。仅限 `mnemosyne-memory==3.15.1`。参数化 SQL，同一事务，不与 `forget()` 捆绑成一个原子操作。正文已消失时重试仍要清理。禁止模糊匹配、清空整表和改 schema。共用行必须失败。P0.2 仍不是完整 GO。
- **身份：先验证版本级 session。** `zhiwo:{memory_id}:r{revision}`，同一个知我数据库，`scope="global"` 只用于内核跨 session 检索。不适用则停止，不引入引用计数或正文标识。
- **保留：接受。** MVP 固定两个上限为 `1000000`，导入前生效。写入前按 Kernel 单个 session 内未巩固行计数，与该 session 的淘汰范围一致，并与写入串行；达到上限则拒绝。这不是整个记忆库的总容量，也不新增容量管理模块。用上限 2 验证第三次新增被拒绝。

2026-09-25：整个 P0 验收为 GO。上面三条决定保持原样。
