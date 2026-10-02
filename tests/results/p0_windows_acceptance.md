# P0 Windows 验收

日期：2026-10-01（本机时间）。工作目录 `D:\zhiwo`。Python：`D:\zhiwo\server\.venv\Scripts\python.exe`。只跑测试，没有改产品代码或测试，没有提交 Git，没有给 `server\.venv` 安装依赖，没有使用真实记忆库或用户自己的 OpenCode 配置。

跑测试时，已跟踪的 `tests/results/*.json` 会被测试自己覆盖。本次因此改写了 `p1_1`、`p1_2`、`p1_3`、`p1_egress`、`p2_1`、`p2_2`、`p2_3`、`p2_3_delivery`、`p2_4`、`review_edges` 这些结果文件。新的 OpenCode 提取结果在 `tests/agent_extract/results_opencode/`。

## 1. 总表

| 步骤 | 命令 | 结果 | 证据 | 原因 |
| --- | --- | --- | --- | --- |
| 0 预检 | `git status --short` | PASS | 见下方清单 | 改动与 P0 清单一致；另有两份未跟踪的竞品分析文档，未改动 |
| 0 预检 | Python、embedding 缓存、`opencode --version`、`pnpm --version` | PASS | 命令输出 | Python 与缓存都在；OpenCode 1.18.33；pnpm 11.21.0 |
| 0 预检 | 在 `server` 下 `python -c "import zhiwo.gateway.stdio_bridge, zhiwo.api.app"` | PASS | 输出 `ok` | 两个模块可以导入 |
| 1 | `python tests\test_propose_scope.py` | PASS | 标准输出：5 tests OK（3.4s）。此测试不写 JSON | 提议范围与请求号单元测试通过 |
| 1 | `python tests\test_client_connect.py` | PASS | `tests/results/client_connect_windows.json`（pass true，14 tests） | 一键连接测试通过 |
| 2 | `python tests\test_p1_1_service.py` | PASS | `tests/results/p1_1_windows.json` | 9.4s，pass true，schema 8，迁移 8 行 |
| 2 | `python tests\test_p1_2_service.py` | PASS | `tests/results/p1_2_windows.json` | 13.9s，pass true |
| 2 | `python tests\test_p1_3_service.py` | PASS | `tests/results/p1_3_windows.json` | 19.3s，pass true |
| 2 | `python tests\test_p1_egress.py` | PASS | `tests/results/p1_egress_windows.json` | 11.2s，pass true |
| 2 | `python tests\test_p2_1_agents.py` | PASS | `tests/results/p2_1_windows.json` | 5.4s，pass true |
| 2 | `python tests\test_p2_2_policy.py` | FAIL | `tests/results/p2_2_windows.json` | 6.2s。真实检索时内核连接已关闭。复现两次。详见第 3 节 |
| 2 | `python tests\test_p2_3_access.py` | PASS | `tests/results/p2_3_windows.json` | 6.5s，pass true，schema 8 |
| 2 | `python tests\test_p2_3_delivery.py` | PASS | `tests/results/p2_3_delivery_windows.json` | 2.0s，pass true，schema 8 |
| 2 | `python tests\test_p3_3_service.py` | PASS | `tests/results/p3_3_windows.json` | 72.6s，pass true |
| 2 | `python -c "import test_review_edges ..."` | PASS | `tests/results/review_edges.json` | 9.7s，退出码 0，`s0_failures` 为空 |
| 3 | `python tests\test_p2_4_client.py` | FAIL | `tests/results/p2_4_windows.json` | 60s。模型可用。失败在扁平参数后的旧调用，以及停用连接的错误采集。详见第 3 节 |
| 4 | `python tests\agent_extract\run_opencode.py` | PASS | `tests/agent_extract/results_opencode/` | A1/A2/C1/C2 全部达到门槛。E1 按已知限制只记录。约 87s |
| 5 | 在 `apps\web` 下 `pnpm build` | PASS | 命令退出码 0（`tsc --noEmit` 后 `vite build`，21.97s） | 类型检查和打包通过。有一条 chunk 超过 500 kB 的警告 |
| 6 | `python tests\test_p3_2_desktop.py` | NOT_RUN | 无 | 按要求跳过安装包测试 |

第 2 步里 10 个命令有 9 个 PASS。有一个 FAIL，所以第 2 步整体是 FAIL。

清单外、本次未改动的文件：`docs/OMNA竞品分析.docx`，`docs/~$NA竞品分析.docx`（Word 锁文件）。

## 2. 第 4 步每个场景

模型 `opencode-go/deepseek-v4-flash`，OpenCode 1.18.33（`D:\npm-global\node_modules\opencode-ai\bin\opencode.exe`）。门槛：成功调用 / 调用次数 ≥ 90%，存下 ≥ 6，类别 ≥ 5，证据逐字 ≥ 90%，回复条数与存下条数一致，正文不把「上周三」推算成具体日期。

| 场景 | 调用 | 成功 | 成功比例 | 存下 | 类别 | 证据逐字 | 回复条数 | 推算日期 | 判定 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A1 | 7 | 7 | 100% | 7 | 6 | 7/7 | 一致（已提交 7 条） | 无，事件仍写「上周三」 | 达到门槛 |
| A2 | 7 | 7 | 100% | 7 | 6 | 7/7 | 一致（已提交 7 条） | 无，事件仍写「上周三」 | 达到门槛 |
| C1 | 8 | 8 | 100% | 8 | 6 | 8/8 | 一致（已提交 8 条） | 无 | 达到门槛 |
| C2 | 10 | 10 | 100% | 10 | 6 | 10/10 | 一致（成功 10 条，失败 0 条） | 无 | 达到门槛 |
| E1 | 0 | 0 | — | 0 | 0 | — | 回复说读不到导入原文 | — | 已知限制，不算失败 |

六个类别都出现了：identity、goal、preference、project、event、other。

A1、A2 用的是一键连接的「可提议修改」：可读类别只有 preference 和 goal，工具是 get_context、search_memory、propose_memory。它们仍然写下了全部 6 类。这和「能读哪些」与「能提哪些」分开的改动一致。

每条存下的 `verification` 都是 `unverified`（这次没有把导入来源 id 交给工具）。逐字判断看的是 `evidence_in_source`，四组都是 100%。

E1 先导入再提取。连接可读全部类别。OpenCode 调用了 `get_context` 和 `search_memory`（都完成），没有调用 `propose_memory`。审计事件 2 条，拒绝 0 条。最后一句话的意思是：导入内容还没变成已确认记忆，所以读不到原文，请把原文贴过来。退出码 0，用时 13.5s。

## 3. FAIL 与 BLOCKED

本次没有 BLOCKED。

### 3.1 第 2 步 `test_p2_2_policy.py`

结果文件 `tests/results/p2_2_windows.json`：`pass` false。

报错：

```text
ProgrammingError: Cannot operate on a closed database.
```

复现两次，堆栈相同。失败点在测试约第 400 行的真实 `search_memory`（前面的检索都用了替身，没有打到内核）：

```text
File "tests/test_p2_2_policy.py", line 400, in test_policy_and_inheritance
    real = search_memory(...)
File "server/zhiwo/services/agent_tools.py", line 283, in _read
    recalled = list(recall_fn(...))
File "server/zhiwo/adapters/kernel_client.py", line 99, in recall_rows
    rows = handle.memory.recall(...)
File "mnemosyne/core/beam.py", line 5748, in recall
    cursor = self.conn.cursor()
sqlite3.ProgrammingError: Cannot operate on a closed database.
```

判断：这是 P0 之前就有的问题，这次改动没有碰到这条路径。

- `kernel_client.py` 没有未提交改动。`_close_transient` 在提交 `d40b56c`（2026-09-26）里，用来关掉短寿命的内核连接。
- 上次记下的通过结果在 `0596e5f`（2026-09-25），`pass` 为 true，比这段关闭逻辑更早。
- Mnemosyne 按线程、按库文件共用一条连接。`write_version` / `read_version` 用完后关掉这条连接，长期的 `KernelHandle` 还握着已经关掉的同一个对象。后面第一次真实 `recall` 就失败。
- P0 对 `agent_tools.py` 的 diff 改的是 `propose_memory` 的请求号和提议类别，检索路径没有改。
- 同一次回归里 `test_p2_3_access.py` 通过，是因为它传入了 `recall` 替身，没有调用 `recall_rows`。

建议修法：在长期句柄上做 `recall` 之前，如果连接已关闭就重新打开；或者让 `_close_transient` 只释放短寿命对象，不要关掉服务句柄仍在使用的那条线程连接。修的是 `server/zhiwo/adapters/kernel_client.py`，不在本次 P0 文件里。

### 3.2 第 3 步 `test_p2_4_client.py`

结果文件 `tests/results/p2_4_windows.json`：`pass` false。OpenCode 四次调用退出码都是 0，模型可用。上次提交的结果是 pass true；其中 `misses: ["r07"]`、`hit_count: 19` 这次一样，而且 `hit_count >= 16`，所以 r07 不是这次的失败项。

这次变失败的字段：

| 字段 | 本次 | 上次通过时 |
| --- | --- | --- |
| `accept_status` | null | 200 |
| `explain_fragment` | false | true |
| `explain_same_miss` | false | true |
| `client_denied_match` | false | true |
| `client_denied_code` | null | FORBIDDEN |

其余抽查项仍为真，包括真实 OpenCode 的搜索、提议（`client_propose_status` = pending，审核 200）和再次搜索。

**提议后的解释链。** 测试约 665–683 行的 stdio 调用仍按旧形状传参：`request_id`、`change` 对象、`evidence` 对象。P0 把工具改成扁平参数（`content`、`category`、`evidence` 字符串等），请求号不再由调用方传入。这次调用过不了新的参数校验，返回里没有 `proposal_id`，后面的批准和 `explain_memory` 都没执行，所以 `accept_status` 保持 null，两个 explain 断言一起失败。同一文件里给 OpenCode 的自然语言提示已经改成扁平字段，那一次提议是成功的。

建议修法：把这段 harness 调用改成扁平参数，带上 `content`、`category`、`kind`、`evidence` 字符串和 `source_ref`，去掉 `request_id` 和 `change` 对象。

**停用后的搜索。** `blocked_code` 仍是 FORBIDDEN，`wrong_code` 仍是 UNAUTHENTICATED。这两次走的是 Python MCP 客户端，能读到工具错误正文。停用连接那一次走 OpenCode，`_collect_tools` 只读 `state.output`。P0 的 `_checked` 在服务返回错误时抛出 MCP `ToolError`，OpenCode 可能把错误放在 `output` 以外，于是错误码是 null，快照也对不上。结果 JSON 没有留下这次的原始 stdout，所以「模型根本没调用工具」这一点没有被单独证伪；和上次通过结果相比，能对上的代码差异就是错误从普通工具结果变成了 MCP 工具错误。

另外，这个测试解析到的客户端是 OpenCode 1.18.16；PATH 上的 `opencode --version` 和第 4 步用的是 1.18.33。测试会优先拿 PATH 旁边 `node_modules\opencode-ai\bin\opencode.exe`。

建议修法：`_collect_tools` 同时读取工具错误正文。产品侧继续用 MCP 错误返回是符合这次改动的。

## 4. 需要手动看的界面

构建已经通过，下面几项要在运行中的 OMNA 里点：

1. 打开 OMNA，进入「我的 Agent」，选一个连接，打开「权限」页。
2. 选「可提议修改」时出现「它可以提哪些」，默认 6 类全选。
3. 切到「只读」时这一组消失。
4. 改动后点保存，刷新页面，设置仍然保持。

代码里这组标题是「它可以提哪些」，预设名是「可提议修改」和「只读」。从「只读」切到「可提议修改」且当前提议类别为空时，会把 6 类都选上。本次没有打开界面，这四项都还没点过。

## 5. 结论

不能提交。

第 1、4、5 步是 PASS。第 2 步因为 `test_p2_2_policy.py` 失败，整体不是 PASS。门槛要求第 1、2、4、5 步全部 PASS。

第 4 步的真实 OpenCode 提取达到了全部数字门槛。第 2 步那个失败在 P0 的 diff 之外，上次通过记录早于关闭内核连接的提交。第 3 步不在提交门槛里，但它的两处失败和 P0 的扁平参数、MCP 错误返回对得上，修测试采集即可，本次按要求没有改代码。
