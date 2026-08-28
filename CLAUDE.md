## Agent 文档同步

`CLAUDE.md` 与 `AGENTS.md` 由 `scripts/sync_agent_docs.py` 在 commit 时自动双向同步。
改任意一侧都可以，但不要在同一次 commit 前同时修改两侧，否则会触发冲突并阻断 commit。
工具专属内容应放在 `<!-- sync:shared:end -->` 之后。

新 clone 仓库后需执行一次：

```sh
git config core.hooksPath .githooks
```

若 `.agents-sync.json` 发生 merge 冲突，删除该文件后重新运行同步即可重建。

已知限制：

- 部分暂存：hook 基于工作区文件同步。若只 stage 了文档的部分改动，工作区完整内容会进入本次 commit。
- `core.hooksPath` 需在每个 clone 中手动启用；未启用时 hook 不会运行。
- 工具不做内容合并；冲突始终交由人工处理。
