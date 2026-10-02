# 测试方案：用户已有 Agent 当提取引擎

## 目标
验证「不配置提取模型，由已连接的 Agent 通过 MCP 调用 propose_memory 生成待确认记忆」能否替代内置提取器，作为非技术用户的默认路径。

## 被测对象
现有代码 HEAD = ab815a7（main）。服务端 server/zhiwo，MCP 入口 gateway/stdio_bridge.py，审核 services/review.py。方案本身尚无专门实现，测试对象是它依赖的现有链路。

## 环境与替身
- 云端 Linux，Python 3.13，mcp 2.2.0，fastapi 0.141.1（从项目 .venv 复制）。
- Mnemosyne 和本地向量模型下载被网络策略拦截（PyPI 403），用内存版 Kernel 替身替换 kernel_client 的读写和召回函数。控制库、权限、审计、审核、HTTP、stdio bridge 全部是真代码。
- stdio bridge 只允许 Windows，测试里把 sys.platform 设为 win32 后启动，其余不改。

## 测试层次
| 层 | 内容 | 判定 |
|---|---|---|
| L0 基线 | 现有 pytest 中不依赖真实 Kernel 的部分 | 记录通过/不可运行 |
| L1 契约 | 直接调用 service 函数，逐条验证代码审查的 9 个疑点 | 每条给出「复现 / 未复现」 |
| L2 接口 | FastAPI TestClient + 真实 stdio bridge + MCP 客户端：tools/list 的 schema、错误是否标 isError、错误信息是否可操作、失败是否进审计 | 同上 |
| L3 旅程 | S1 一键连接后提取一段自我介绍；S2 导入未配模型后交给 Agent；S3 同一段文本提取两次；S4 通过后 explain_memory；S5 更新已有记忆 | 每个旅程给出完成率和断点 |
| L4 故障注入 | 模拟模型常见偏差：中文类别、复用示例 UUID、非 UUID、多余字段、缺字段、改写过的证据、超长内容 | 是否被接受、报错能否指导改正、是否审计 |
| L5 真实客户端 | Claude Code CLI 通过真实 stdio bridge 连接，用自然语言让它提取；跑默认模型和 Haiku 两档 | 候选落库率、调用次数、Agent 对用户的说法是否与实际一致 |

## 上线门槛（本方案作为默认路径）
1. 一键连接后，L5 中候选落库率 ≥ 90%，六个类别都能提交。
2. 没有静默失败：Agent 说「已保存」时，待确认里一定有对应条目。
3. 导入的来源与 Agent 提案关联，导入任务状态正确，证据可核实。
4. 同一段文本重复提取，不产生重复待确认。
5. 所有拒绝都写进访问记录，错误信息指明字段和允许值。
