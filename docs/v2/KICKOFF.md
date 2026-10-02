# 给编程 Agent 的开场指令

在 `D:\zhiwo` 下新开一个编程会话（需在这台 Windows 电脑上运行），把下面整段发给它。

```
你是 OMNA v2 的开发负责人。工作目录 D:\zhiwo，原生 Windows。

先读：AGENTS.md、PRODUCT.md、ARCHITECTURE.md、docs/v2/PLAN.md，
再看 docs/v2/design/README.md、design/screenshots/ 下的截图和 design/source/ 下的设计稿源码。

本次授权（已由我批准，属于基线变更）：
- 按 PLAN.md「基线变更」一节修改 PRODUCT / ARCHITECTURE / AGENTS / README；
- 实施 PLAN.md「v2.0 功能范围 · 做」里的全部内容；
- 「不做」和「延后」里的内容一律不做，设计稿里出现了也不做。

执行顺序：
1. 先提交当前未提交的 P0 与 D1–D4 改动，打标签 v1.1，新建 v2 分支。
   docs/v2/ 这个目录也一并提交。
2. 按 PLAN.md 的排期分阶段推进，每完成一项按 AGENTS.md §6 汇报：
   完成什么、怎么验证、还有什么问题、下一步。
3. 第 2 周末的「拆分质量门」：用合成说明文件跑测试，把结果报给我，等我决定再继续。
4. 不改数据库结构（保持 schema 8）；需要改时先停下来说明原因。
5. 测试只用合成数据和临时目录，不碰我真实的记忆库和客户端配置。

现在先做第 1 步，然后列出第 1 周的具体任务清单给我确认。
```
