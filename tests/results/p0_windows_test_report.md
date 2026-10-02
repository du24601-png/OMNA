# OMNA P0 Windows 测试报告

| 项目 | 内容 |
| --- | --- |
| 产品 | OMNA（代码目录名 zhiwo） |
| 被测变更 | P0：已有 AI 助手把信息提交给 OMNA，用户确认后入库 |
| 日期 | 2026-10-01 |
| 环境 | Windows 11（10.0.26200），原生 Windows |
| Python | `D:\zhiwo\server\.venv\Scripts\python.exe`（3.12.13） |
| 内核 | Mnemosyne 3.15.1，本地 embedding 缓存 `experiments\kernel_spike\runs\p0_3\fastembed-cache` |
| 客户端 | OpenCode 1.18.33；模型 `opencode-go/deepseek-v4-flash` |
| 前端 | pnpm 11.21.0 |
| 数据 | 全部使用临时目录。未使用真实记忆库，未改用户自己的 OpenCode 配置 |
| 执行约束 | 只跑测试。未改产品代码和测试，未提交 Git，未安装或升级 `server\.venv` 依赖 |

原始逐步记录见 `tests/results/p0_windows_acceptance.md`。

## 结论

**不能提交。**

提交门槛是第 1、2、4、5 步全部 PASS。第 1、4、5 步通过。第 2 步有 1 项失败（`test_p2_2_policy.py`），整步记 FAIL。

真实 OpenCode 提取（第 4 步）达到全部数字门槛。第 3 步的真实客户端接入失败，这一步不在提交门槛里。安装包测试按要求未跑。权限页的四项界面操作需要人工点选，本次没有打开界面。

## 被测内容

1. `propose_memory` 改为扁平、带类型的参数，类别是枚举。出错时返回 MCP 错误，写明字段和允许值。服务说明里增加提取规则。
2. 请求号由服务端按内容生成。同一条建议重复提交只留一条。
3. 连接权限拆成「能读哪些类别」和「能提哪些类别」。数据库 schema 从 7 升到 8。「可提议修改」预设：读偏好和目标，可在全部 6 类提建议。
4. 前端「我的 Agent」权限页增加「它可以提哪些」。

工作区改动与上述清单一致。清单外有两份未跟踪文件，本次未改动：`docs/OMNA竞品分析.docx`、`docs/~$NA竞品分析.docx`（Word 锁文件）。

## 结果汇总

| 步骤 | 内容 | 结果 | 说明 |
| --- | --- | --- | --- |
| 0 | 预检：Git 清单、Python、缓存、OpenCode、pnpm、模块导入 | PASS | 导入输出 `ok` |
| 1 | 新增单元测试 | PASS | `test_propose_scope.py` 5 项；`test_client_connect.py` 14 项 |
| 2 | 真实内核回归，10 个命令 | FAIL | 9 个 PASS，`test_p2_2_policy.py` FAIL |
| 3 | 真实 OpenCode 接入 `test_p2_4_client.py` | FAIL | 模型可用。两处断言失败 |
| 4 | 真实 OpenCode 提取 A1、A2、C1、C2、E1 | PASS | A1–C2 达到门槛。E1 为已知限制 |
| 5 | `apps\web` 下 `pnpm build` | PASS | `tsc --noEmit` 与 vite build 退出码 0。有 chunk 超过 500 kB 的警告 |
| 6 | 打包安装版 `test_p3_2_desktop.py` | NOT_RUN | 按要求跳过 |

没有 BLOCKED。

### 第 2 步明细

| 命令 | 用时 | 结果 | 证据 |
| --- | --- | --- | --- |
| `tests\test_p1_1_service.py` | 9.4s | PASS | `tests/results/p1_1_windows.json`（schema 8，迁移 8 行） |
| `tests\test_p1_2_service.py` | 13.9s | PASS | `tests/results/p1_2_windows.json` |
| `tests\test_p1_3_service.py` | 19.3s | PASS | `tests/results/p1_3_windows.json` |
| `tests\test_p1_egress.py` | 11.2s | PASS | `tests/results/p1_egress_windows.json` |
| `tests\test_p2_1_agents.py` | 5.4s | PASS | `tests/results/p2_1_windows.json` |
| `tests\test_p2_2_policy.py` | 6.2s | FAIL | `tests/results/p2_2_windows.json` |
| `tests\test_p2_3_access.py` | 6.5s | PASS | `tests/results/p2_3_windows.json`（schema 8） |
| `tests\test_p2_3_delivery.py` | 2.0s | PASS | `tests/results/p2_3_delivery_windows.json`（schema 8） |
| `tests\test_p3_3_service.py` | 72.6s | PASS | `tests/results/p3_3_windows.json` |
| `test_review_edges` | 9.7s | PASS | `tests/results/review_edges.json`（`s0_failures` 为空） |

schema 从 7 升到 8 在 `p1_1`、`p2_3_access`、`p2_3_delivery` 的结果里得到确认。

## 真实 OpenCode 提取

证据目录：`tests/agent_extract/results_opencode/`。整次约 87 秒。客户端为 OpenCode 1.18.33。

门槛：成功调用 / 调用次数 ≥ 90%；存下 ≥ 6 条；类别至少 5 种；`evidence_in_source` 为 true 的比例 ≥ 90%；回复里说的条数与存下条数一致；正文不把原文里的「上周三」推算成具体日期。

| 场景 | 调用 | 成功 | 存下 | 类别数 | 证据逐字 | 回复 | 推算日期 | 判定 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A1 一键连接，默认权限 | 7 | 7 | 7 | 6 | 7/7 | 「已提交 7 条」 | 无 | 达到门槛 |
| A2 同上，再跑一次 | 7 | 7 | 7 | 6 | 7/7 | 「已提交 7 条」 | 无 | 达到门槛 |
| C1 开放全部类别 | 8 | 8 | 8 | 6 | 8/8 | 「已提交 8 条」 | 无 | 达到门槛 |
| C2 同上，再跑一次 | 10 | 10 | 10 | 6 | 10/10 | 「成功 10 条，失败 0 条」 | 无 | 达到门槛 |
| E1 先导入再提取 | 0 | 0 | 0 | 0 | — | 读不到导入原文 | — | 已知限制，不算失败 |

六类都出现过：identity、goal、preference、project、event、other。事件正文保留「上周三」，没有具体公历日期。

A1、A2 的可读类别只有 preference 和 goal，工具是 `get_context`、`search_memory`、`propose_memory`。它们仍然写下了全部 6 类，与「能读哪些」和「能提哪些」分开一致。

存下的每条 `verification` 都是 `unverified`，因为这次没有把导入来源 id 交给工具。逐字比例按 `evidence_in_source` 计算，四组都是 100%。

E1：连接可读全部类别。OpenCode 调用了 `get_context` 和 `search_memory`，没有调用 `propose_memory`。审计 2 条，拒绝 0 条。回复说明导入内容还没变成已确认记忆，所以读不到原文。退出码 0，用时 13.5 秒。

## 缺陷

### D1　发布之后的真实检索使用已关闭的内核连接

| 项 | 内容 |
| --- | --- |
| 用例 | `tests/test_p2_2_policy.py` |
| 结果 | FAIL，复现两次，堆栈相同 |
| 证据 | `tests/results/p2_2_windows.json` |
| 归属 | P0 之前已存在。`server/zhiwo/adapters/kernel_client.py` 没有未提交改动 |
| 是否挡住提交 | 是。它使第 2 步整体 FAIL |

报错：

```text
sqlite3.ProgrammingError: Cannot operate on a closed database.
```

失败在测试约第 400 行。这是该用例里第一次真正调用内核检索；前面的检索使用替身，没有打到内核。

```text
test_p2_2_policy.py:400  search_memory
agent_tools.py:283       _read → recall_rows
kernel_client.py:99      handle.memory.recall
mnemosyne beam.py:5748   self.conn.cursor()
```

Mnemosyne 对同一线程、同一库文件共用一条连接。用例先发布和审核记忆，`write_version` / `read_version` 结束时 `_close_transient` 关掉这条连接。长期的 `KernelHandle` 仍握着同一个已关闭对象，随后第一次 `recall` 失败。

`_close_transient` 进入仓库的提交是 `d40b56c`（2026-09-26）。上一次记下的通过结果在 `0596e5f`（2026-09-25）。P0 对 `agent_tools.py` 的改动在提议参数和请求号，检索路径未改。同一次回归中 `test_p2_3_access.py` 通过，因为它传入了 `recall` 替身，没有调用 `recall_rows`。

建议：在长期句柄上检索之前，连接已关闭就重新打开；或者 `_close_transient` 只释放短寿命对象，保留服务句柄仍在使用的那条连接。

### D2　真实客户端测试仍按旧形状调用 `propose_memory`

| 项 | 内容 |
| --- | --- |
| 用例 | `tests/test_p2_4_client.py`，约 665–683 行的 stdio 调用 |
| 结果 | FAIL |
| 证据 | `tests/results/p2_4_windows.json` |
| 归属 | 跟 P0 的扁平参数一致。产品侧的自然语言提议在同一用例里成功 |
| 是否挡住提交 | 否。第 3 步不在提交门槛内 |

OpenCode 四次进程退出码都是 0，模型可用。失败字段：

| 字段 | 本次 | 上次通过记录 |
| --- | --- | --- |
| `accept_status` | null | 200 |
| `explain_fragment` | false | true |
| `explain_same_miss` | false | true |

这段调用仍传 `request_id`、`change` 对象和 `evidence` 对象。P0 的工具参数是扁平的 `content`、`category`、`evidence` 字符串，请求号由服务端生成。参数校验失败后没有 `proposal_id`，批准和 `explain_memory` 都没有执行。同一文件里给 OpenCode 的提示已经改成扁平字段，那一次提议状态是 pending，审核返回 200。

`misses` 仍含 `r07`，`hit_count` 为 19。上次通过记录也是这两项，且门槛是 `hit_count >= 16`，所以 r07 不是本次新失败。

建议：把这段 harness 调用改成 `content`、`category`、`kind`、`evidence` 字符串和 `source_ref`，去掉 `request_id` 和 `change` 对象。

### D3　停用连接后，OpenCode 侧没有采到错误码

| 项 | 内容 |
| --- | --- |
| 用例 | `tests/test_p2_4_client.py`，停用连接后的 `search_memory` |
| 结果 | `client_denied_match` false，`client_denied_code` null |
| 证据 | `tests/results/p2_4_windows.json` |
| 归属 | 与 P0 把服务错误改成 MCP `ToolError` 对得上 |
| 是否挡住提交 | 否 |

同一用例里，Python MCP 客户端仍读到 `blocked_code = FORBIDDEN`、`wrong_code = UNAUTHENTICATED`。停用连接那一次走 OpenCode，采集函数 `_collect_tools` 只读 `state.output`。服务错误现在以 MCP `ToolError` 返回，错误正文可能不在 `output` 里。结果 JSON 没有保留这次原始 stdout，因此不能单独证明模型调用了该工具。和上次通过记录相比，对得上的代码差异是错误从普通工具结果变成了 MCP 工具错误。

这个用例解析到的 OpenCode 是 1.18.16。PATH 上的 `opencode --version` 和第 4 步使用的是 1.18.33。用例会优先选择 PATH 旁边的 `node_modules\opencode-ai\bin\opencode.exe`。

建议：`_collect_tools` 同时读取工具错误正文。产品侧继续返回 MCP 错误。

## 未在本次执行的检查

权限页需要在运行中的 OMNA 里人工确认：

1. 打开「我的 Agent」，选一个连接，进入「权限」。
2. 选择「可提议修改」后出现「它可以提哪些」，默认 6 类全选。
3. 切换到「只读」后这一组消失。
4. 保存后刷新页面，设置仍然保持。

代码中的标题和预设名与上面四句一致。从「只读」切到「可提议修改」、且当前提议类别为空时，会选中全部 6 类。这四项本次没有点过。

安装包测试 `tests\test_p3_2_desktop.py` 记为 NOT_RUN。

## 附注

测试运行改写了已跟踪的结果文件：`tests/results/` 下的 `p1_1_windows.json`、`p1_2_windows.json`、`p1_3_windows.json`、`p1_egress_windows.json`、`p2_1_windows.json`、`p2_2_windows.json`、`p2_3_windows.json`、`p2_3_delivery_windows.json`、`p2_4_windows.json`、`review_edges.json`。新的提取结果在 `tests/agent_extract/results_opencode/`。这些文件都没有提交。
