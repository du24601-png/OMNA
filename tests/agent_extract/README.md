# 已有 Agent 当提取引擎：P0 测试

**MOCK 标识：** 本目录的 `conftest.py` 和 `serve.py` 把 Mnemosyne 换成 `fakekernel.py`（同名表的小 SQLite）。控制库、权限、审计、审核、HTTP、stdio bridge 是真代码。按 AGENTS.md §5，这里的结果不是 Kernel 验收或 Windows 真实客户端验收证据。

| 文件 | 内容 |
|---|---|
| `TEST_PLAN.md` | 测试方案 |
| `test_agent_extract.py` | T01–T10 契约、接口与偏差注入；T04、T06、T07b 是 P1，标 xfail(strict) |
| `run_real_client.py` | L5：Claude Code CLI 经一键连接写出的配置和真实 stdio bridge 接入，用一句自然语言让它提取 |
| `run_opencode.py` / `.bat` | 同一场景在原生 Windows + 真实 Kernel + OpenCode 上跑（未运行） |
| `results/` | P0 之后的结果；`results/before_p0/` 是改动前 |

运行（Linux，`bridge_shim.py` 只绕过 bridge 的 Windows 检查）：

```
pytest tests/agent_extract tests/test_propose_scope.py -q
python tests/agent_extract/run_real_client.py A1 sonnet preset
```
