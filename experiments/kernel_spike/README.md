# P0 · Kernel Spike

Mnemosyne 能力验证脚本与实测记录目录。任务定义见根目录 [PLAN.md](../../PLAN.md) 第 3 节。

- 使用独立数据目录 `runs/`，勿指向本机已有 Mnemosyne 默认库。
- 命令、版本、结果写入 `results/`；并在 `PLAN.md` 进度表中追加证据路径。

## P0.1

在 `experiments/kernel_spike/` 下：

```powershell
uv python pin 3.12
uv lock
uv sync
uv run python p0_1_roundtrip.py
```

脚本会清空并使用 `runs/p0_1/`，用新进程读回一条合成中文记忆。P0.1 当时只验证主键读回。当前锁文件在此基础上增加了 `mnemosyne-memory[embeddings]==3.15.1`，供 P0.3 本地向量使用。

## P0.2

```powershell
uv python pin 3.12.13
uv lock
uv sync
uv run python p0_2_boundaries.py
```

样本在 `fixtures/p0_2_samples.json`。脚本使用 `runs/p0_2/`，并检查本机默认 Mnemosyne 路径没有新增或改动文件。结果在 `results/p0_2_windows.json`。

补验删除残留、条数淘汰和操作身份：

```powershell
uv run python p0_2_followup.py
```

结果在 `results/p0_2_followup.json`。

版本 session、派生表清理和写入上限：

```powershell
uv run python p0_2_session.py
```

结果在 `results/p0_2_session.json`。P0.2 补验已通过。条数上限只约束单个 session 里尚未巩固的行。

## P0.3

```powershell
uv run python p0_3_recall.py
```

样本在 `fixtures/p0_3_queries.json`。20 条记忆写进同一个数据库，每条使用 `zhiwo:{memory_id}:r1`，由一个读取端调用 `recall()`。本地模型是 `BAAI/bge-small-zh-v1.5`。结果在 `results/p0_3_windows.json`：直接表达 10/10，改写 9/10。失败例是 `r07`。

首次在线下载该模型时，Hugging Face 的 TLS 握手中断，fastembed 没有继续尝试 Qdrant 的 GCS 地址。后来手工下载 `fast-bge-small-zh-v1.5.tar.gz`（54584282 字节）并放进 `runs/p0_3/fastembed-cache/`。安装体验留到产品化阶段。

## P0.4

```powershell
uv run python p0_4_run.py
```

最小入口是 `p0_4_stdio.py`，只暴露 `search_memory`。它使用 P0.3 召回前的数据库副本和已经放好的本地模型缓存。结果在 `results/p0_4_windows.json`。本轮真实客户端是 OpenCode 1.18.16。

合格结果计数：

```powershell
.\.venv\Scripts\python.exe -m unittest test_search_filter.py
```

`search_published()` 先过滤再按 `limit` 计数。Kernel 召回窗口是 40，不是响应里的 `limit`。结果在 `results/p0_5_filter.json`。这次没有重跑 P0.1 到 P0.4。
