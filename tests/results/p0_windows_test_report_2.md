# OMNA P0 Windows 测试报告（复测）

| 项目 | 内容 |
| --- | --- |
| 产品 | OMNA（代码目录名 zhiwo） |
| 被测变更 | P0 复测。上一轮 D1、D2、D3 已修，另加并发检索 D4 |
| 日期 | 2026-10-01 |
| 环境 | Windows 11（10.0.26200），原生 Windows |
| Python | `D:\zhiwo\server\.venv\Scripts\python.exe`（3.12.13） |
| 内核 | Mnemosyne 3.15.1，本地 embedding 缓存 `experiments\kernel_spike\runs\p0_3\fastembed-cache` |
| 客户端 | 提取场景 OpenCode 1.18.33；`test_p2_4_client.py` 解析到 OpenCode 1.18.16。模型 `opencode-go/deepseek-v4-flash` |
| 数据 | 全部使用临时目录。未使用真实记忆库，未改用户自己的 OpenCode 配置 |
| 执行约束 | 只跑测试。未改产品代码和测试，未提交 Git，未安装或升级 `server\.venv` 依赖 |

上一轮报告：`tests/results/p0_windows_test_report.md`。

## 结论

**可以提交。**

第 2 到第 6 步全部 PASS。内核句柄回归连跑 3 次都输出 OK。上一轮失败的 `test_p2_2_policy.py` 和 `test_p2_4_client.py` 这次通过。提取抽查 A1、C1 达到上一轮的数字门槛。

权限页四项仍需人工点选，不计入这次的提交门槛。安装包测试这次没有安排，记为未跑。

## 被测内容

相对上一轮，这次多出来的改动：

1. `server/zhiwo/adapters/kernel_client.py`：服务句柄独占自己的连接；检索加锁串行。对应 D1、D4。
2. `tests/test_p2_4_client.py`：直接调用改为扁平参数；OpenCode 的错误文本从 `state.error` 读取。对应 D2、D3。
3. 新增 `tests/test_kernel_handle.py`：D1 和 D4 的回归，使用真实 Mnemosyne。
4. `ARCHITECTURE.md` 补了一句说明。

`git status --short` 里这四个路径都在。同时仍有上一轮的 P0 改动，以及上次测试改写的 `tests/results/*.json`、两份报告、`tests/agent_extract/`。清单外未改动：`docs/OMNA竞品分析.docx`、`docs/~$NA竞品分析.docx`。

## 结果汇总

| 步骤 | 内容 | 结果 | 说明 |
| --- | --- | --- | --- |
| 1 | `git status --short` | PASS | 四个修复文件都在改动里 |
| 2 | `tests\test_kernel_handle.py` 连跑 3 次 | PASS | 每次输出 `OK`，退出码 0。约 3.4s、3.6s、3.3s |
| 3 | `tests\test_propose_scope.py` | PASS | 5 tests OK，3.3s |
| 3 | `tests\test_client_connect.py` | PASS | 14 tests OK，6.6s。`tests/results/client_connect_windows.json` pass true |
| 4 | 真实内核回归，10 个命令 | PASS | 全部退出码 0 |
| 5 | `tests\test_p2_4_client.py` | PASS | 74.5s。`tests/results/p2_4_windows.json` pass true |
| 6 | `tests\agent_extract\run_opencode.py A1 C1` | PASS | 约 45s。A1、C1 都达到门槛 |

没有 FAIL，没有 BLOCKED。

### 第 4 步明细

| 命令 | 用时 | 结果 | 证据 |
| --- | --- | --- | --- |
| `tests\test_p1_1_service.py` | 6.4s | PASS | `tests/results/p1_1_windows.json`（schema 8，pass true） |
| `tests\test_p1_2_service.py` | 13.5s | PASS | `tests/results/p1_2_windows.json` |
| `tests\test_p1_3_service.py` | 19.3s | PASS | `tests/results/p1_3_windows.json` |
| `tests\test_p1_egress.py` | 11.4s | PASS | `tests/results/p1_egress_windows.json` |
| `tests\test_p2_1_agents.py` | 5.6s | PASS | `tests/results/p2_1_windows.json` |
| `tests\test_p2_2_policy.py` | 7.5s | PASS | `tests/results/p2_2_windows.json`（pass true。上一轮是 FAIL） |
| `tests\test_p2_3_access.py` | 5.9s | PASS | `tests/results/p2_3_windows.json`（schema 8） |
| `tests\test_p2_3_delivery.py` | 1.9s | PASS | `tests/results/p2_3_delivery_windows.json`（schema 8） |
| `tests\test_p3_3_service.py` | 73.2s | PASS | `tests/results/p3_3_windows.json` |
| `test_review_edges` | 19.5s | PASS | `tests/results/review_edges.json`（输出 OK，`s0_failures` 为空） |

`test_kernel_handle.py` 覆盖两件事：同一线程上短寿命写入之后，服务句柄仍能检索；启动线程删除之后，4 个工作线程并发写入并检索，主线程再检索仍能看到至少 6 条。

## 真实 OpenCode 提取

证据：`tests/agent_extract/results_opencode/A1.json`、`C1.json`。客户端 OpenCode 1.18.33，模型 `opencode-go/deepseek-v4-flash`。

门槛与上一轮相同：成功调用 / 调用次数 ≥ 90%；存下 ≥ 6 条；类别至少 5 种；`evidence_in_source` 为 true 的比例 ≥ 90%；回复里说的条数与存下条数一致；正文不把「上周三」推算成具体日期。

| 场景 | 调用 | 成功 | 存下 | 类别数 | 证据逐字 | 回复 | 推算日期 | 判定 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A1 一键连接，默认权限 | 7 | 7 | 7 | 6 | 7/7 | 「已提交 7 条」 | 无，事件仍写「上周三」 | 达到门槛 |
| C1 开放全部类别 | 7 | 7 | 7 | 6 | 7/7 | 「已提交 7 条」 | 无，事件仍写「上周三」 | 达到门槛 |

六类都出现了：identity、goal、preference、project、event、other。

A1 的可读类别只有 preference 和 goal，工具是 `get_context`、`search_memory`、`propose_memory`。它仍然写下了全部 6 类。

## 上一轮缺陷的复测

| 编号 | 上一轮 | 这次 |
| --- | --- | --- |
| D1 发布后真实检索碰到已关闭的内核连接 | `test_p2_2_policy.py` FAIL | `test_kernel_handle.py` 3 次 OK；`test_p2_2_policy.py` pass true |
| D2 直接调用仍用旧的 `propose_memory` 参数 | `accept_status` null，解释断言失败 | `accept_status` 200，`explain_fragment` true，`explain_same_miss` true |
| D3 停用连接后 OpenCode 侧采不到错误码 | `client_denied_code` null | `client_denied_code` 为 FORBIDDEN，`client_denied_match` true |
| D4 并发检索 | 上一轮未单列失败 | 含在 `test_kernel_handle.py` 的 4 线程并发用例里，3 次都 OK |

`test_p2_4_client.py` 四次 OpenCode 退出码都是 0。`client_propose_status` 为 pending，审核返回 200。`blocked_code` 为 FORBIDDEN，`wrong_code` 为 UNAUTHENTICATED。`hit_count` 为 19，`misses` 仍有 `r07`。用例自己的门槛是 `hit_count >= 16`，所以上一轮同样存在的 r07 这次也不使该步失败。

## 需要人工点的界面

这次没有打开 OMNA。请在运行中的界面确认：

1. 打开「我的 Agent」，选一个连接，进入「权限」。
2. 选择「可提议修改」后出现「它可以提哪些」，默认 6 类全选。
3. 切换到「只读」后这一组消失。
4. 保存后刷新页面，设置仍然保持。

## 附注

这次测试再次改写了已跟踪的 `tests/results/` 下 `p1_1_windows.json`、`p1_2_windows.json`、`p1_3_windows.json`、`p1_egress_windows.json`、`p2_1_windows.json`、`p2_2_windows.json`、`p2_3_windows.json`、`p2_3_delivery_windows.json`、`p2_4_windows.json`、`review_edges.json`，并更新了 `tests/agent_extract/results_opencode/` 里的 A1、C1。这些文件都没有提交。

`test_p2_4_client.py` 用的 OpenCode 是 1.18.16，提取脚本用的是 PATH 上的 1.18.33。两处都跑通了。
