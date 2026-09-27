# 知我 · PLAN

> V1.0 启动基线｜更新日期：2026-09-27  
> 本文是唯一进度入口。范围见 [PRODUCT.md](PRODUCT.md)，实现约束见 [ARCHITECTURE.md](ARCHITECTURE.md)，执行规则见 [AGENTS.md](AGENTS.md)。

## 1. 当前状态

| 项目 | 状态 |
| --- | --- |
| 已有输入 | 产品方案、四份启动文档、P0.1 原生 Windows 存取证据 |
| 工程完成情况 | P0 验收结论为 GO。P1.1 至 P1.5 已通过。P2.1 至 P2.4 已通过各自范围。P3.1 界面收尾已通过。客户端列表可写入 WorkBuddy、ZCode、OpenCode、ChatGPT 的配置。真实工具调用仍只验收了 OpenCode |
| 当前阶段 | **P3 · 产品化，P3.1 与 P3.3 已完成** |
| 当前任务 | **四个客户端的配置写入已完成。P3.2 仍暂停** |
| 下一任务 | P3.4 最终验收。安装包仍等负责人通知，见第 8 节 |
| 当前风险 | 改写查询 `r07` 未进前五。检索会带回目标以外的记忆。模型首次下载需手工缓存。A12 的完全断网仍未验。A11 的删除中断已在 P3.3 补验 |

2026-09-27 负责人要求改关于我首页，不在 P3.2 内：左栏卡片墙跟随主题，悬停时其余变暗；右上近期变化；右下读取趋势。整页不滚动，多出来的卡片只在左栏里滚，滚动条在移入或聚焦时才出现。计数接口不返回正文。服务测试见 `tests/test_access_reads.py`。

2026-09-27 负责人另行授权摘要层 V1：关于我增加一份 Owner 手动生成的 AI 摘要。实现只读取 Mnemosyne 中当前有效、已确认的完整记忆集合；`zhiwo.db.profile_summary` 只保存一条可重建缓存。记忆指纹变化后显示待更新，生成期间发生变化则拒绝覆盖，失败保留旧摘要，永久删除与清空会清掉缓存。摘要不进入 MCP。服务测试见 `tests/test_profile_summary.py`。真实云模型生成内容尚未验收。

同日后续：右上「近期变化」撤下，该位置改为 AI 摘要卡片。读取趋势增加近 24 个本地小时。卡片不再写「允许已授权 Agent 读取」，仅自己可见时才标出。标志固定在画面左上，与导航同一高度。右上「导入」和「添加记忆」合并为石墨黑的「＋ 添加」。摘要更新时弹窗有彩色光晕，更新按钮不用橙色。浏览器走查了这些界面；真实云模型生成仍未验收。

2026-09-27 负责人要求记忆列表能继续浏览：同一列按创建时间每次 50 条，底部查看更早或更新的，表头区分已显示和总数。搜索仍只列出最相关的 20 条。服务测试见 `tests/test_memory_pages.py`。不在 P3.2 内。

P0.1 使用 uv 管理的 CPython 3.12.13。本机自带 Python 3.9.10 不满足 `mnemosyne-memory` 的 `>=3.10` 要求。

任务状态统一为：`TODO / DOING / BLOCKED / DONE`。没有实际证据不得标 `DONE`；缺 Windows 环境时记录 `NOT_RUN`，不能用 Linux 或 WSL 测试代替原生 Windows 验收。

## 2. 开发顺序

| 阶段 | 要回答的问题 | 可检查交付物 | 当前状态 |
| --- | --- | --- | --- |
| P0 内核验证 | 选定 Kernel 能否守住产品边界？ | 锁定版本、最小脚本、中文样本、实测结果、继续/阻塞结论 | DONE |
| P1 核心闭环 | 用户能否导入、审核、看到可纠正的记忆？ | 本地 Web + 真实双库 + 审核/版本/画像 | DONE |
| P2 真实接入 | Agent 能否按授权取得记忆并提出变更？ | 一个真实 MCP 客户端、四个工具、授权与访问记录 | DONE |
| P3 产品化 | 普通目标用户能否在 Windows 上完成整个流程？ | 四页界面、Electron 安装包、备份恢复与异常处理 | DOING |

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

- [x] **P2.1** 实现连接创建、专用凭证、类别/工具授权、停用及重置；Agent 不能访问 Owner API。本轮只成立身份和权限管理。记忆逐条过滤、真实 MCP 和访问记录留在 P2.2–P2.4。证据 `tests/results/p2_1_windows.json`。
- [x] **P2.2** 实现四个工具与统一权限检查。读取先过滤再限量；提案只生成候选；解释只返回获准版本的已审核片段。访问记录留在 P2.3，真实客户端留在 P2.4。证据 `tests/results/p2_2_windows.json`。这不是 Agent 接入全部通过。
- [x] **P2.3** 实现访问快照与“我的 Agent”页面。快照与返回载荷是同一份；写入失败不返回记忆正文。交付按服务生成的事件号更新。新建连接显示待验证，已停用优先显示。证据 `tests/results/p2_3_windows.json` 与 `tests/results/p2_3_delivery_windows.json`。
- [x] **P2.4** 用 OpenCode 1.18.16 完成查询、提案、用户审核、再次查询和停用后拒绝。stdio bridge 调用现有服务。A03 的 MCP 部分与 A06–A10 通过，样本沿用 P0.3。证据 `tests/results/p2_4_windows.json`。A12 完全断网仍是 `NOT_RUN`。这不是安装包验收。

**交付：** 能展示“这个 Agent 本次得到这几条信息，其他类别没有提供”，并能证明停用后下一次调用被拒绝。无需第二个客户端才能验收。

## 6. P3 · 桌面产品化

**前置：P2 通过。**

- [x] **P3.1** 统一四页、详情抽屉、Diff、空态、加载、错误、键盘焦点与基本视觉；检查 1024 px 和 1440 px。证据 `tests/results/p3_1_windows.json`，截图 `tests/results/p3_1/`。发送锁复核见 `tests/results/p3_1_lock_windows.json`。Electron 未开始。
- [ ] **P3.2** Electron 管理 Python 服务；打出 Windows 安装包，在无开发环境的测试用户/机器上安装验证。明确首次模型准备的网络与磁盘需求。
- [x] **P3.3** 完成模型设置、可读导出、配套备份/恢复、永久删除与清空；恢复后不复活旧 Agent 凭证。证据 `tests/results/p3_3_windows.json`。A12 完全断网和 P3.2 不在本轮。
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

## 8. 当前任务卡：P3.2

**状态：TODO。** 目标：普通用户装好安装包后双击即可使用，不需要 Python、Node、uv 或手工输入凭证。

接手核查（2026-09-25）：工作区干净，最新提交 `db7750a`。`apps/web` 的 `tsc --noEmit` 通过；`tests/test_owner_search_filter.py` 与 `tests/test_p3_1_send_gate.py` 复跑通过。`apps/desktop/` 还不存在。Owner API 目前没有 `DELETE /memories/{id}`、`GET /imports/{id}`、设置、导出、备份、恢复和清空路由，这些属于 P3.3。

现状中与打包直接相关的约束：

| 现状 | 打包时要处理的问题 |
| --- | --- |
| 服务启动要求 `ZHIWO_DATA_DIR`、`ZHIWO_OWNER_CREDENTIAL`，端口固定 8765 | 由 main 进程生成数据目录和随机 Owner 凭证；端口被占用时要明确报错，不能连到别的进程 |
| 网页在登录门里手工输入 Owner 凭证，存在 `sessionStorage` | Electron 内由 preload 交给渲染层，用户不再输入；本地 Web 开发方式保持不变 |
| 提取地址和模型名可写入设置；密钥用 Windows DPAPI 写在数据目录的 `extractor.key`，不进业务库 | 子进程只传白名单环境变量，不继承宿主环境里的密钥和 `MNEMOSYNE_*`。页面没保存过时才沿用环境变量 |
| `mcp_runtime` 返回 `sys.executable` 与仓库路径 | 安装版要指向随包 Python 和资源目录，复制出的客户端配置在安装目录下可用 |
| 向量模型首次从 Hugging Face 下载时 TLS 中断过 | 首次模型准备方式待负责人决定，见下 |

范围内：

1. `apps/desktop/`：main、preload、服务子进程管理（健康检查等待、单实例、退出时结束子进程树）、启动失败页。渲染层关闭 Node 集成，开启 context isolation。
2. 随包 Python 运行时与 `server/uv.lock` 里的依赖，版本不变。
3. Windows 安装包，记录安装包体积、安装后磁盘占用和首次模型准备所需的网络与磁盘。
4. 服务日志写到数据目录下的日志文件，不含凭证和记忆正文。

不在范围：设置页、密钥保存、导出、备份、恢复、删除（P3.3）；自动更新、代码签名、第二个客户端。

负责人决定（2026-09-25）：

- 向量模型 `BAAI/bge-small-zh-v1.5` 随安装包提供，首次使用不联网。安装包体积和安装后占用照实记录。
- 本轮先做到能出安装包。在没有开发环境的机器上安装验证记为 `NOT_RUN`，P3.2 在补验之前不标 `DONE`。
- 2026-09-25 负责人叫停：安装包还没打出，开发尚未结束，P3.2 先不做。没有留下 Electron 代码。

验收：在没有开发工具的环境里安装、启动、保存并搜索一条合成记忆；关闭后没有残留的 Python 进程；重启后数据仍在。用安装版的 bridge 让 OpenCode 调一次 `search_memory`。首次模型准备单独记录；完全断网仍按 A12 另记。证据写入 `tests/results/p3_2_windows.json`。

### P3.3 结果

P3.3 已通过。证据是 `tests/results/p3_3_windows.json`。设置页从左栏底部进入，四个主页面不变。提取密钥用 Windows DPAPI 写在数据目录，不出现在接口响应或 `zhiwo.db`。导出不含密钥。恢复后旧 Agent 凭证失效，环境变量里的提取密钥不会自动回来。清空后记忆不再出现，数据目录以外的备份文件仍在。删除在标记之后中断，重启能继续，关联来源被整份删除，另一条记忆保留。浏览器走查了左下角设置、保存模型配置后看不到密钥，以及删除确认。A12 完全断网和 P3.2 仍未做。

### 四个客户端配置写入

负责人要求第一版只跑通 WorkBuddy、ZCode、OpenCode 和 Codex。「我的 Agent」顶部是这四行。检测到已安装后，确认写入才合并一条 `zhiwo` 到该客户端自己的配置。页面显示「已连接 n / 4」和「配置已写入」。「刷新连接」重读文件。凭证不出现在接口响应里。证据是 `tests/results/client_connect_windows.json`。浏览器在独立临时目录里走查：四行都是未连接，确认写入 WorkBuddy 后变为「配置已写入」和「已连接 1 / 4」，刷新后仍在，连接卡片是「待验证」。这一轮没有改用户本机上已有的四份配置，也没有在这四个客户端里再做一次真实工具调用。真实调用仍只有 P2.4 的 OpenCode。

### 「我的 Agent」合成一个连接列表

负责人要求降低普通用户的理解门槛：状态靠标志和颜色自己说明，深入设置点开再看。客户端列表和连接卡片合成左侧一个列表，右侧是详情。状态只剩未连接、待验证、已验证、已停用、未安装五种。写入配置后，页面可见时每 4 秒查一次本机服务，最多三分钟，收到真实调用就自动变成已验证。权限页先用一句话总结，再给只读或可提议修改两种选择、类别标签，四个工具的开关收进「逐项设置工具」。停用改成详情顶部的开关；重新写入配置和重置凭证放进 ⋯ 菜单并要确认。重新写入会先按当前能力选预设，再把原来的工具和类别写回。接口多了两个只读字段：客户端列表的 `agent_id`，连接的 `last_access_at`。

验证：`tests/test_client_connect.py` 6 项通过，新增一项检查 `agent_id` 只指向该客户端写入的连接、同名自定义连接不会被认成它，响应不含凭证。前端 `tsc` 与构建通过。浏览器在独立临时数据目录和假的用户目录里走查：连接 WorkBuddy 后出现三步进度与测试问题；模拟一次带 stdio 标记的工具调用后，页面几秒内自动变成已验证并显示最近访问时间；改权限后出现保存条，保存后生效；停用后列表显示空心点；重新写入配置后工具和类别保持不变；自定义连接创建后显示一次性配置。模拟调用不算真实客户端验收，真实调用仍只有 P2.4 的 OpenCode。`test_p2_1_agents.py` 等会重写证据文件的脚本这次没有重跑。

### 访问记录按句子折叠

访问记录默认显示拿到的句子，不再一行一次调用。同一种操作、距这组最新一次不超过 5 分钟的调用并成一组，同一句只留一次。没有返回内容的收在当天底部。次数和发送状态在点开之后。四种操作用 Lucide（ISC）的图标区分：搜索、书本、铅笔、引号。列表接口增加 `returned`，从已有快照裁出来，不新存字段。`tests/test_access_summary.py` 检查截断，以及拒绝原因和没有正文的提案不会被当成句子。

### P3.1 之后的记录

P3.1 已通过。证据是 `tests/results/p3_1_windows.json`，截图在 `tests/results/p3_1/`。四页连到真实服务。1024 px 与 1440 px 没有横向溢出。A12 完全断网仍是 `NOT_RUN`。本轮没有做删除、设置页或 Electron 打包。下一步才是 P3.2，本轮没有开始。

P3.1 之后，记忆页改成左侧筛选加右侧单列卡片，颜色改为白底浅灰边。约定写在 `PRODUCT.md` 第 5.1 节。这次只改版式，没有新功能，也没有把 P3.2 标成开始。

2026-09-27 负责人要求按演示把待确认改成单列列表：Agent 图标在每条左侧上下居中；普通新增点对勾保存、点叉号拒绝；修改、相似、过长和缺少依据不能一键覆盖。分组、搜索、多选、批量保存和稍后处理从这一页拿掉。随后去掉演示说明，类别改成带图标的小标签，点一条在原行展开。P3.2 仍未开始。

### P3.1 结果

合成数据，独立测试库，本地模型缓存。页面标明「合成演示数据」。结果文件不含凭证。`pass` 是 true。

| 检查 | 结果 |
| --- | --- |
| 关于我 | 空态只有一条添加引导。保存「日常沟通时，我更喜欢简洁、直接的回答。」后出现在偏好。编辑后版本 2 为当前，版本 1 仍可回看 |
| 导入与审核 | 粘贴提取出目标和偏好两条候选。确认目标后队列减为 1，并给出查看正式记忆。Markdown 文件「计划.md」再放入 1 条待确认。无效字节被拒绝，草稿保留 |
| 我的 Agent | 中文名称「林舟的写作助手」完整显示。新建为「待验证」。授权偏好和搜索后停用，状态优先显示「已停用」 |
| 失败与冲突 | 服务停止时草稿仍在，提示本地服务不可用，不声称模型未配置。恢复后服务状态回到正常。编辑期间另存版本后保存被拒绝，输入保留 |
| 键盘 | 有未保存修改时按 Esc 会询问；拒绝离开则详情和草稿仍在 |
| 发送锁 | 两个异步请求不能在同一事件循环线程重入提交锁。撤销会等待正在发送的响应。见 `tests/results/p3_1_lock_windows.json` |

### P2.4 结果

OpenCode 1.18.16，模型 `opencode-go/deepseek-v4-flash`。独立进程 `zhiwo.gateway.stdio_bridge` 携带 Agent 凭证调用本机 HTTP 服务，环境里没有数据目录。临时数据目录，本地模型缓存，`HF_HUB_OFFLINE=1`。结果文件不含凭证。`a12` 与 `electron` 都是 `NOT_RUN`。`pass` 是 true。

| 检查 | 结果 |
| --- | --- |
| 工具 | stdio 只列出 `get_context`、`search_memory`、`propose_memory`、`explain_memory` |
| 真实客户端 | 调用前 `client_status` 是 `pending`，响应里没有「已连接」。搜索成功后变为 `verified`。客户端收到的载荷与访问快照一致，交付状态是 `sent`，命中「周末喜欢骑公路自行车，单次大约四十公里。」 |
| 提案与再查 | `propose_memory` 返回 `pending`。Owner 拒绝后再次搜索仍有原句，不含「所有回答都越短越好。」快照与载荷一致 |
| 停用 | 停用后下一次搜索是 `FORBIDDEN`，载荷与快照一致，不含原句。页面状态优先显示「已停用」 |
| A03 MCP | 待审「紫水晶计划」和已拒绝「青金石口令」不在 MCP 搜索、Owner 搜索和画像里 |
| A06 | 固定样本 Hit@5 为 19/20。漏召回是 `r07`。无关查询返回 0 条。过期句子没有混进这些结果 |
| A07 | 只有偏好权限的连接能看到偏好句，看不到项目句。无权限是 `FORBIDDEN`，伪造凭证是 `UNAUTHENTICATED`，直调 Owner API 是 `401`。这些响应都没有对应正文 |
| A08 | 停止共享和已过期的句子不再返回。检索进行中的撤销见 `tests/results/p2_3_delivery_windows.json`：最终检查后、交给通道前撤销的响应不含原文；锁被占用时撤销会等待 |
| A09 | 解释只返回「已审核片段：只解释这一句。」来源里的其他句子和旧版本句子不在响应里。未获准与不存在都是 `memory not found` |
| A10 | 客户端载荷与快照一致。日志失败是 `AUDIT_UNAVAILABLE` 且不留下快照。发送中断是 `failed`，结果不确定是 `unknown`，都没有标成 `sent`，也没有「已阅读」「已采用」 |

### P2.3 交付收尾

证据是 `tests/results/p2_3_delivery_windows.json`。`pass` 是 true。

| 检查 | 结果 |
| --- | --- |
| 事件号 | 不同 Agent 使用同一请求号得到不同事件。一次交付通知只改变对应的一条。同一 Agent 重试再产生一条，不影响前一条 |
| 状态转换 | `prepared` 可以变成 `failed`。之后再标 `sent` 是 409，记录保持 `failed` |
| 提交失败 | 快照事务的 `commit()` 失败返回 `AUDIT_UNAVAILABLE`，不返回记忆正文，也不留下记录 |
| 发送 | HTTP 写出被连接中断时状态是 `failed`。结果无法判断时是 `unknown`。快照字节不变 |
| 撤销顺序 | 最终检查已经写入含原文的快照后、交给通道前完成撤销，发出的响应和快照都不含原文。撤销若抢不到锁，会等到这次写出结束；下一次响应不再含原文 |
| 连接状态 | 不带 stdio 标记的成功调用仍是 `pending`。stdio 成功交付后是 `verified`。已停用连接不会因此变成已验证 |

### P2.3 结果

进程内函数加真实 Owner HTTP。临时数据目录，本地模型缓存，`HF_HUB_OFFLINE=1`。schema 版本 6。结果文件不含凭证。`real_client` 和 `a12` 都是 `NOT_RUN`。`stage_complete` 是 false。

| 检查 | 结果 |
| --- | --- |
| 审核意图 | 私有且有有效期的记忆，HTTP 请求不带共享和有效期字段时，两项限制仍在。保存的意图里这两个字段都标为未提供。相同意图重放返回原版本。明确清空或明确提交共享状态是 409，版本不变 |
| 提案归属 | 两个 Agent 使用同一个请求号，得到不同提案。一方变成已通过后，另一方重试仍是自己的待确认。同一 Agent 的相同载荷重放，不同载荷 409 |
| 过滤 | 超长单条被跳过，后面的短句仍返回。缺少生命周期状态的行不返回 |
| 提交顺序 | 快照插入尚未提交时，另一连接看不到这行；函数返回后，快照与返回的是同一个对象。状态先是已准备。标成发送失败后，载荷不变，也不会被改成已交给发送通道 |
| 撤销之后的响应 | 检索中关闭共享，当次返回空列表。撤销完成后再检索，返回和快照都不含已失去共享的正文 |
| 凭证重置 | 请求期间重置返回 401，权限版本加 1，错误里没有记忆正文。已停用连接再重置仍停用，版本从 3 变为 4 |
| 快照失败 | `AUDIT_UNAVAILABLE`，不返回正文，也不留下快照 |
| HTTP 交付 | 搜索响应与快照一致，交付状态是 `sent`。列表不含整份响应，详情里的响应与快照一致。新建连接的 `client_status` 是 `pending`，响应里没有「已连接」 |

P2.2 已通过统一权限检查和四个工具。证据是 `tests/results/p2_2_windows.json`。真实客户端已由 P2.4 补上。

### P2.2 结果

进程内调用正式函数，临时数据目录，本地模型缓存，`HF_HUB_OFFLINE=1`。合成句子只用于探针。结果文件不含凭证。

| 检查 | 结果 |
| --- | --- |
| 控制属性继承 | 私有且有效期为 `2099-06-01T00:00:00+00:00` 的记忆，只批准正文后仍是私有，有效期不变。明确打开共享并清空有效期后才改变。只改有效期时共享仍关闭。带目标的编辑同样继承。`share_enabled: null` 是 400，版本不变 |
| 新增 | 接受候选时共享默认为开，有效期取候选 `2097-03-01T00:00:00+00:00`。请求明确关闭共享时保持私有 |
| 同一过滤 | `get_context` 与 `search_memory` 对同一召回窗口只返回可共享的当前版本。私有、未授权类别和未发布行不出现。无权工具不触发召回 |
| 限量 | 超过 2000 字的条目整条丢弃并标 `truncated`，不截成半条。`limit=1` 只返回一条并标截断。召回只调用一次 |
| 真实召回与 bridge | 精确句子“权限探针：橙色文件夹放在第二层。”由正式召回和 `mcp_bridge.forward` 命中。未知工具和多余参数是 400 |
| 解释 | 证据是“已审核片段。”，来源类型是 `paste`。响应没有整份来源后半、旧正文、新正文或旧片段。无权与不存在都是 404，消息同为 `memory not found` |
| 提案 | 状态保持 `pending`，`memory_refs` 数量不变。相同载荷重放，不同载荷 409。私有目标和缺失目标都是 `memory not found`。能核对的片段标 `matched`；对不上的说明是“Agent 提供，未核实”。声明来源只存片段 |
| 请求期间撤销 | 检索完成后、提交前分别关闭共享、更新正文、停用连接、重置凭证。前两种返回空列表，不含新正文。停用是 403 `agent is disabled`。重置是 401 `agent credential rejected`，`policy_version` 加 1。已停用连接再重置仍停用，版本变为 4。本人仍能读到私有记忆 |

`access_log` 与 `real_client` 都是 `NOT_RUN`。`stage_complete` 是 false。

P2.1 已通过，范围只限连接身份和权限管理。证据是 `tests/results/p2_1_windows.json`。凭证重置改为递增 `policy_version` 后，这份结果已重跑。记忆逐条过滤已在 P2.2 完成。真实 MCP 客户端和访问记录仍按 P2.3–P2.4 推进。本轮没有做 Agent 页面、删除、设置页或 Electron。

### P2.1 结果

正式 FastAPI，监听 `127.0.0.1`，`ZHIWO_KERNEL_CONNECT_ONLY=1`，临时数据目录。合成名称是“合成连接甲”和“合成连接乙”。明文凭证只在创建或重置的当次响应里返回；列表、日志、数据库字节和 `tests/results/p2_1_windows.json` 都不含明文。

| 检查 | 结果 |
| --- | --- |
| 独立凭证 | 两条连接的 ID 不同，凭证哈希不同。库里只存 sha256 |
| 默认无权限 | 工具和类别都是空数组，启用，`policy_version` 为 1 |
| 伪造身份 | 查询参数中的 `agent_id`、`name` 和请求头 `X-Agent-Name` 不改变身份。返回 `identity_source=credential` |
| Agent 调用 Owner API | 读取记忆、提案、来源和连接，以及写入记忆、停用连接、重置凭证，都是 `401 UNAUTHENTICATED`，响应没有记忆条目。这些调用之后连接仍启用，`policy_version` 仍是 1。`memory_refs` 仍是 0 |
| 未知值 | 未知工具和未知类别都是 `400 VALIDATION_ERROR`，权限版本不变 |
| 权限版本 | 授予 `search_memory` 和 `preference` 后为 2。只改名称仍是 2。同一请求重放不重复增加。停用后为 3。重置凭证同样加 1，变为 4，并且不把已停用连接重新启用。再次启用后为 5 |
| 停用与重置 | 停用后会话是 `403 FORBIDDEN`。重置后旧凭证是 `401`，新凭证在连接仍停用时是 `403`。重放重置不返回第二次明文 |
| 重启 | 进程号变化。启用、名称、工具、类别和 `policy_version=5` 不变。新凭证仍可识别，旧凭证仍是 `401` |

`tests/results/p2_1_windows.json` 里的 `memory_filter`、`mcp`、`access_log` 仍是 `NOT_RUN`。过滤和四个工具由 P2.2 另测；真实客户端和访问记录仍未做。

### P1 有限补验

| 检查 | 结果 |
| --- | --- |
| 外网受限、保留回环 | `tests/results/p1_egress_windows.json`。新进程；启动前模型缓存已在 `experiments/kernel_spike/runs/p0_3/fastembed-cache`。`HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY` 指向 `http://127.0.0.1:9`，`NO_PROXY` 保留 `127.0.0.1,localhost`，`HF_HUB_OFFLINE=1`。访问 `https://example.com` 得到 `URLError`。保存、读取和搜索“外网受限探针：回环仍可保存这条合成偏好。”成功 |
| A12 完全断网 | `NOT_RUN`。本轮尝试 `New-NetFirewallRule` 拦截出站，返回 `Access is denied`，进程没有管理员权限，规则没有创建。见 `tests/results/a12_full_disconnect.json`。先前到 `1.1.1.1:443` 的原始 TCP 仍然连通。代理黑洞不是完全断网 |
| A03 | 不重跑。待审核、已拒绝正文不进入正式搜索。引用 `tests/results/a03_owner_search_citation.json`。MCP 仍是 `NOT_RUN` |
| TXT、Markdown | 接口通过、文件选择框交互未验证 |

### P1.5 结果

直接编辑已确认记忆走 `PATCH /api/v1/memories/{id}`，进入 `publish_memory()`，不创建提案。更新载荷包含 `source_refs`。真实提取使用 DeepSeek `deepseek-flash`，`ZHIWO_TEST_MODE=0`。超时和无效格式来自本机测试端点，与真实提取分开记录。

| 检查 | 结果 |
| --- | --- |
| A01、A05 直接编辑 | 添加“日常回复偏好简洁”后，在详情里改为“日常回复偏好先给结论”并勾选“仅自己可见”。页面说明这次修改直接生效、没有进入待确认。版本 1 为历史，版本 2 为当前。画像同步为新正文。重启后仍是版本 2。截图 `tests/results/p1_5/p1_5_direct_edit.png`、`p1_5_restart.png`、`p1_5_profile_after_review.png` |
| A02 粘贴、TXT、Markdown | 粘贴在页面完成。TXT `notes.txt` 与 Markdown `plan.md`：接口通过、文件选择框交互未验证。样本提交的是 `POST /api/v1/imports`。只批准部分候选。批准项带来源。同一 `Idempotency-Key` 再提交不增加来源或候选。样本见 `tests/results/p1_5_extract.json` |
| A03 未批准内容 | 待审核、已拒绝正文不进入正式搜索。待确认“最近准备转 AI 产品”，以及已拒绝的“正式报告需要详细论据”“周宁每周三晚上进行面试辅导。”“发布说明用完整句子。”，不在记忆列表和画像中，搜索命中不含这些正文。证据直接复用，不重跑，汇录在 `tests/results/a03_owner_search_citation.json`。`tests/results/p1_2_windows.json` 的 `candidate_search_count` 为 0，是另一份样本。MCP 查询 `NOT_RUN` |
| A04、A11 | 沿用 `tests/results/p1_3_windows.json`：`update_conflict`、`keep_both`、审核重放，以及 kernel/commit 两处中断恢复均为通过。本轮没有重跑。删除中断仍是 P3，`NOT_RUN` |
| 真实提取 | 服务商 DeepSeek，模型 `deepseek-flash`。原文、四条候选和证据见 `tests/results/p1_5_extract.json`。人工核对后，“日常回复希望简洁”没有被写成“所有场景都简洁”。批准身份和这条偏好，拒绝“正式报告需要详细论据”，目标留在待确认。画像只有已批准内容 |
| A12 未配置 | 来源保留，状态 `extractor_unavailable`，提案 0 条。页面写明提取模型未配置。截图 `p1_5_import_unconfigured.png` |
| A12 超时 | 本机测试端点，不是真实模型。`error_code=TIMEOUT`。页面写明提取超时、来源仍在、可以重试。重试后仍是一条来源、零条提案。截图 `p1_5_timeout.png` |
| A12 无效格式 | 本机测试端点，不是真实模型。`error_code=VALIDATION_ERROR`。页面写明格式无效、来源仍在、可以重试。重试后仍是一条来源、零条提案。截图 `p1_5_invalid.png` |
| A12 断网 | P1.5 把 `HTTPS_PROXY` 指向 `127.0.0.1:9`，对外请求没有 HTTP 响应。本地仍能保存、读取并搜索“断网探针：书桌靠窗”。同一进程里的云提取返回 `MODEL_UNAVAILABLE`，来源保留，正式检索没有该正文。截图 `p1_5_offline_search.png`、`p1_5_offline_extract.png`。2026-09-25 的正式服务补验见 `tests/results/p1_egress_windows.json`：回环保留，保存、读取和搜索成功。完全断网仍是 `NOT_RUN` / 待补验；该次到 `1.1.1.1:443` 的原始 TCP 仍然连通 |

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
| 2026-09-25 | Owner 闭环修正 | DONE | `tests/test_owner_search_filter.py` | 当前搜索先按已发布版本、类别和有效期过滤再截断；仅自己可见仍留在本人列表。待确认的更新和编辑后保存提交修改后的正文，并带上所选记忆。同一段来源文本复用同一个导入键。 |
| 2026-09-25 | P2.1 | DONE | `tests/results/p2_1_windows.json` | 连接身份和权限管理通过。凭证重置现已递增 `policy_version`，结果已重跑。真实 MCP 和访问记录未做。下一步已进入 P2.3 |
| 2026-09-25 | P2.2 | DONE | `tests/results/p2_2_windows.json` | 四个工具和统一权限检查通过。访问记录与真实客户端当时是 `NOT_RUN`。不是 Agent 接入全部通过 |
| 2026-09-25 | P2.3 | DONE | `tests/results/p2_3_windows.json`；`tests/results/p2_3_delivery_windows.json` | 访问快照与“我的 Agent”页面通过。交付改为按事件号更新，提交失败和发送中断已补验。真实客户端当时留在 P2.4 |
| 2026-09-25 | P2.4 | DONE | `tests/results/p2_4_windows.json` | OpenCode 1.18.16 走通查询、提案、拒绝、再查和停用。A06 为 19/20，漏召回 `r07`。A12 完全断网与 Electron 仍是 `NOT_RUN`。下一步是 P3.1 |
| 2026-09-25 | P3.1 | DONE | `tests/results/p3_1_windows.json`；截图 `tests/results/p3_1/`；`tests/results/p3_1_lock_windows.json` | 四页视觉与交互收尾通过。1024 px 与 1440 px 无横向溢出。A12 完全断网与 Electron 仍未做。下一步是 P3.2 |
| 2026-09-25 | 界面控件与动效 | DONE | 新增依赖 `@base-ui/react`（可访问控件）与 `motion`（反馈动效），写入 `pnpm-lock.yaml` 与 ARCHITECTURE 第 1 节 | 只改界面，没有新功能 |
| 2026-09-25 | 记忆页版式 | DONE | 预览走查：主题筛选、搜索、详情、过期空态；`apps/web` 下 `tsc --noEmit` | 未另存验收文件。当前任务仍是 P3.2，Electron 未开始 |
| 2026-09-25 | P1 有限补验 | DONE | `tests/results/p1_egress_windows.json`；`tests/results/a03_owner_search_citation.json` | 外网 HTTP 受限且回环保留时，保存、读取、搜索成功。A12 完全断网仍待补验。A03 复用已有结果，MCP 未跑。TXT/Markdown 文件选择框未验证 |
| 2026-09-25 | 项目接手核查 | DONE | `tsc --noEmit`；`tests/test_owner_search_filter.py`；`tests/test_p3_1_send_gate.py`；第 8 节 P3.2 任务卡 | 只同步文档，没有改代码。负责人决定：模型随安装包提供；无开发环境验证先记 `NOT_RUN`；P3.2 等通知再开工 |
| 2026-09-25 | P3.2 叫停 | TODO | 安装包未产出。已撤回服务里未完成的静态页挂载 | 开发尚未结束，先不做 Electron。模型随包和干净机器验证的决定仍保留 |
| 2026-09-25 | P3.3 | DONE | `tests/results/p3_3_windows.json` | 设置、导出、备份、恢复、清空和删除中断后继续已在 Windows 上通过。密钥不在响应和 `zhiwo.db`。恢复后旧 Agent 凭证失效。A12 完全断网和安装包仍未做 |
| 2026-09-25 | 四个客户端配置写入 | DONE | `tests/results/client_connect_windows.json` | WorkBuddy、ZCode、OpenCode、Codex 的配置合并已在临时目录通过。凭证不在响应和业务库。真实客户端工具调用仍只有 P2.4 的 OpenCode |
| 2026-09-26 | 顶栏导航 | DONE | 浏览器走查：四个主页面、设置、导入、添加记忆、详情；1024 px 与 1440 px 无横向溢出 | 左栏改为顶部。四个主页面居中，选中为浅灰底。当前任务仍是 P3.2 |
| 2026-09-26 | 顶栏 Dock | DONE | 浏览器走查：四个主页面默认无文字，悬停放大并显示名称，点击仍能切换 | 设置、导入、添加记忆仍带文字。当前任务仍是 P3.2 |
| 2026-09-26 | 设置弹窗 | DONE | 浏览器走查：从记忆页打开设置，切换四个分区，关闭后仍留在记忆页 | 提取、备份、恢复和清空的操作没有改。当前任务仍是 P3.2 |
| 2026-09-26 | 设置进入 Dock | DONE | 浏览器走查：设置图标在中间 Dock，悬停显示名称，点击打开弹窗 | 右侧只留导入和添加记忆。当前任务仍是 P3.2 |
| 2026-09-26 | 详情展开 | DONE | 浏览器走查：首页卡片展开到右侧详情再缩回；记忆列表同样展开；Esc 关闭；编辑后取消仍留在详情 | 没有来源卡片时仍从右侧滑入。当前任务仍是 P3.2 |
| 2026-09-26 | 详情居中 | DONE | 浏览器走查：卡片展开到画面中央，周围模糊，点击遮罩关闭 | 当前任务仍是 P3.2 |
| 2026-09-26 | 详情遮罩 | DONE | 浏览器走查：详情遮罩与添加记忆弹窗使用同一模糊 | 当前任务仍是 P3.2 |
| 2026-09-26 | 详情内文 | DONE | 浏览器走查：句子在上，属性一行，来源和版本分开，删除说明在确认时出现 | 当前任务仍是 P3.2 |
| 2026-09-26 | 设置主题 | DONE | 浏览器走查：主题分区可切换浅色/深色和橙蓝绿紫，刷新后仍在 | 只存在本机浏览器。当前任务仍是 P3.2 |
| 2026-09-26 | 深色显示 | DONE | 浏览器走查：首页卡片、记忆列表、设置表单、添加记忆和详情；浅色橙色对照仍是白底 | 深色改为分层表面和更亮主色。当前任务仍是 P3.2 |
| 2026-09-26 | 深色卡片描边 | DONE | 浏览器走查：记忆列表卡片边框为透明，靠底色分开 | 空位虚线仍保留。当前任务仍是 P3.2 |
| 2026-09-26 | OMNA 标志 | DONE | 浏览器走查：顶栏横向标志在深色为白色、浅色为黑色 | 定稿在 `brand/`。当前任务仍是 P3.2 |
| 2026-09-26 | 设置界面 | DONE | 浏览器走查：设置去掉顶栏，左侧分组，提取模型显示能否提取 | 分区和操作没有增减。当前任务仍是 P3.2 |
| 2026-09-26 | 记忆列表 | DONE | 浏览器走查：记忆页改成分列列表，点一行仍打开详情 | 没有增加批量选择。当前任务仍是 P3.2 |
| 2026-09-26 | 列表筛选排序 | DONE | 浏览器走查：列表上方筛选可按主题收窄，并和左侧筛选同步；排序可改为旧的在前；Esc 关闭 | 用已有的 Base UI 弹出层和菜单。当前任务仍是 P3.2 |
| 2026-09-26 | 记忆列表布局 | DONE | 浏览器走查：去掉左侧筛选栏，搜索在列表上方右侧，列表居中；筛选改回分组列表，状态选全部后再选主题可看该主题全部记忆 | 当前任务仍是 P3.2 |
| 2026-09-26 | 记忆来源列 | DONE | 浏览器走查：主题和时间之间有来源列，标志加名称；本产品添加的显示 OMNA，对不上连接的 Agent 提案显示 Agent 提案 | 标志放在 apps/web/public/sources。当前任务仍是 P3.2 |
| 2026-09-26 | Claude 连接 | DONE | `tests/test_client_connect.py`：临时目录合并 Claude 桌面版和 Claude Code，原有配置保留，凭证不进响应 | 没有改用户本机配置，也没有在 Claude 里做真实工具调用。当前任务仍是 P3.2 |
| 2026-09-26 | 客户端列表标志 | DONE | 浏览器走查：本机客户端每一行名称前有对应标志，六枚都已加载 | 当前任务仍是 P3.2 |
| 2026-09-26 | WorkBuddy 标志 | DONE | 浏览器走查：连接列表的 WorkBuddy 换成用户提供的绿色图标，记忆来源使用同一张图 | 当前任务仍是 P3.2 |
| 2026-09-26 | ChatGPT 名称与标志 | DONE | `tests/test_client_connect.py`：列表名称为 ChatGPT，原 Codex 连接改名且不改其他连接。浏览器走查连接列表 | 配置仍写 `~/.codex/config.toml`。没有改用户本机配置。当前任务仍是 P3.2 |
| 2026-09-26 | 记忆导航图标 | DONE | 浏览器走查：顶栏记忆按钮换成圆柱数据库图标，当前页高亮正常 | 当前任务仍是 P3.2 |
| 2026-09-26 | 按来源筛选 | DONE | `tests/test_origin_filter.py`：OMNA、OpenCode、未关联提案互相隔离。浏览器走查：选 OpenCode 只剩该来源，选 Claude 为空，清除后恢复 | 当前任务仍是 P3.2 |
| 2026-09-26 | 记忆列表时间 | DONE | 浏览器走查：今天显示「今天 HH:mm」，跨过零点显示「昨天 HH:mm」，悬停有完整时间，列内不换行 | 当前任务仍是 P3.2 |
| 2026-09-26 | 去掉整条顶栏 | DONE | 浏览器走查：五个按钮固定在顶部正中，悬停仍放大并显示名称；标志、导入、添加记忆只在关于我；记忆页不再出现这三项；1024 px 互不重叠 | 当前任务仍是 P3.2 |
| 2026-09-26 | 关于我标志固定 | DONE | 浏览器走查：关于我页滚到 480 px 后标志仍在左上角，卡片从下方经过；记忆页没有这枚标志 | 当前任务仍是 P3.2 |
| 2026-09-26 | 圆角顶栏 | DONE | 浏览器走查：五个按钮是白底圆角浮条，当前项为浅灰块，悬停仍放大并显示名称；标志、导入、添加记忆在关于我卡片页内并随页面滚动；记忆页没有这三项；添加记忆可打开再取消 | 当前任务仍是 P3.2 |
| 2026-09-26 | 加载骨架 | DONE | 浏览器走查：关于我在取回前是四张卡片骨架，标志和导入、添加记忆仍在；记忆页在取回前是六行列表骨架，筛选和搜索仍在。取回后骨架换成真实内容 | 当前任务仍是 P3.2 |
| 2026-09-26 | 数据目录打开 | DONE | `tests/test_data_dir_picker.py` 7 项通过。独立页面走查：点路径打开当前目录；文件夹按钮弹出选择框，取消后路径不变，确定后打开所选文件夹并说明记忆库位置没改 | 正在跑的 8765 进程还是旧代码，要重启后这个按钮才在当前页面生效。当前任务仍是 P3.2 |
| 2026-09-27 | 关于我分栏 | DONE | `tests/test_access_reads.py`。浏览器走查：左栏跟随主题，右下为真实读取次数；左栏滚动条默认隐藏、移入才出现；整页不再出现滚动条 | 不在 P3.2 内。当前任务仍是 P3.2 |
| 2026-09-27 | 关于我摘要与首页操作 | DONE | `tests/test_profile_summary.py`；`tests/test_access_reads.py`。浏览器走查：摘要弹窗、24 小时趋势、左上标志、「＋ 添加」打开添加和导入 | 真实云模型生成未验收。不在 P3.2 内。当前任务仍是 P3.2 |
| 2026-09-27 | 记忆列表续页 | DONE | `tests/test_memory_pages.py` 5 项通过。浏览器走查：18 条时表头为「记忆 18 条」，没有继续查看；把首页临时改成 5 条后出现「已显示 5 条，共 18 条」和「查看更早的」，点下去补齐后回到「记忆 18 条」。旧的在前会倒序。搜索一条时不出现「最相关的 20 条」 | 正式仍是每次 50 条。不在 P3.2 内。当前任务仍是 P3.2 |
| 2026-09-27 | 记忆列表滚动条 | DONE | 浏览器走查：滑块宽 8 px，平时透明，移入和滚动时为浅灰，没有系统箭头 | 不在 P3.2 内。当前任务仍是 P3.2 |
| 2026-09-27 | 待确认同宽 | DONE | 浏览器走查：待确认页与记忆列表同为 960 px，左右对齐 | 不在 P3.2 内。当前任务仍是 P3.2 |

待验证能力记录：

| 能力 | 状态 | 证据 / 阻塞 |
| --- | --- | --- |
| 精确依赖 + Windows 原生存取 | DONE | Windows 10.0.26200；CPython 3.12.13（`.python-version` 与 `uv.lock` 均为 `==3.12.13`）；`mnemosyne-memory==3.15.1`。见 `experiments/kernel_spike/results/p0_1_windows.json` |
| 自动整理隔离、历史/删除、幂等定位 | DONE | 版本 session 与派生表清理见 `experiments/kernel_spike/results/p0_2_session.json`。条数上限是单个 session 内未巩固行，不是全库容量。向量索引删除见 `p0_3_windows.json` |
| 中文本地召回 + 断网 | DONE | 模型 `BAAI/bge-small-zh-v1.5`，`fastembed==0.8.1`，`sqlite-vec==0.1.9`。统一 `recall()` Hit@5 为 19/20。见 `experiments/kernel_spike/results/p0_3_windows.json` |
| 一个真实客户端 + stdio 凭证链路 | DONE | P0 实验只注册 `search_memory`，见 `experiments/kernel_spike/results/p0_4_windows.json`。P2.4 的正式 bridge 注册四个工具并调用现有服务，见 `tests/results/p2_4_windows.json`。OpenCode 1.18.16，`mcp==2.2.0` |
| 本机服务、控制库、Owner 认证、Adapter 连接 | DONE | `tests/results/p1_1_windows.json`。连接测试不加载向量模型 |
| 手动保存、来源、导入候选 | DONE | `tests/results/p1_2_windows.json`。云模型提取 `NOT_RUN` |
| 审核、版本冲突、失败恢复 | DONE | `tests/results/p1_3_windows.json`。云模型提取仍 `NOT_RUN` |
| 关于我、记忆、待确认 | DONE | `tests/results/p1_4_windows.json`。云模型提取仍 `NOT_RUN` |
| Owner 验收与真实提取 | DONE | `tests/results/p1_5_windows.json`；`tests/results/p1_5_extract.json`。DeepSeek `deepseek-flash`。MCP 与删除恢复未跑 |
| 四个工具与统一权限检查 | DONE | `tests/results/p2_2_windows.json`。后续收口见 P2.3 |
| 访问快照与我的 Agent | DONE | `tests/results/p2_3_windows.json`；交付收尾 `tests/results/p2_3_delivery_windows.json`。A12 完全断网仍是 `NOT_RUN`，见 `tests/results/a12_full_disconnect.json` |
| 真实客户端查询、提案、审核、停用 | DONE | `tests/results/p2_4_windows.json`。A06 为 19/20，漏召回 `r07`。只验收 OpenCode |
| 四页界面收尾 | DONE | `tests/results/p3_1_windows.json`；截图 `tests/results/p3_1/`。发送锁见 `tests/results/p3_1_lock_windows.json`。Electron 未开始 |

范围或架构变更先记录：**问题证据 → 最小可行选项 → 推荐方案 → 对范围/数据/计划的影响 → 用户决定**。决策通过后再同步相关文档，不在实现中悄悄改变基线。

P0.2 决定（2026-09-24，实施前记录）：

- **派生表清理：批准。** Adapter 可在 `forget()` 后删除 `gists.memory_id` 与 `memoria_facts.source_memory_id` 等于目标 kernel id 的行。仅限 `mnemosyne-memory==3.15.1`。参数化 SQL，同一事务，不与 `forget()` 捆绑成一个原子操作。正文已消失时重试仍要清理。禁止模糊匹配、清空整表和改 schema。共用行必须失败。P0.2 仍不是完整 GO。
- **身份：先验证版本级 session。** `zhiwo:{memory_id}:r{revision}`，同一个知我数据库，`scope="global"` 只用于内核跨 session 检索。不适用则停止，不引入引用计数或正文标识。
- **保留：接受。** MVP 固定两个上限为 `1000000`，导入前生效。写入前按 Kernel 单个 session 内未巩固行计数，与该 session 的淘汰范围一致，并与写入串行；达到上限则拒绝。这不是整个记忆库的总容量，也不新增容量管理模块。用上限 2 验证第三次新增被拒绝。

2026-09-25：整个 P0 验收为 GO。上面三条决定保持原样。
