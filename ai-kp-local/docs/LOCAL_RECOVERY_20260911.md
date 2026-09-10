# 本地代码恢复记录 — 2026-09-11

## 基线与范围

用户提供最后一次 push 日期为 2026-08-07。新 clone 的 origin 为
`https://github.com/eThAN02010506/board_game.git`，main 的 HEAD 为
`bc8d5fd29efb73f013605b599ef2d0b5ad46f348`，提交日期与用户记忆一致。
开始恢复时内层仓库工作区干净。

实际仓库：`/Users/ethanjiang/Developer/board_game/board_game`。
恢复分支：`codex/recover-local-20260911`。外层同名目录还有一个无提交的
Git 仓库，本次未改动它；编辑器应打开上述内层仓库。

来源为此前本地工作 / 测试副本的永久归档，SHA-256：
`74c3b0bcb303f0ad78531d8ca3f214153684f1038c63e6a92d5fea663c356108`。
永久备份目录：`/Users/ethanjiang/Developer/board_game-recovery-20260910.OnVcKS`。
其中 downloaded-baseline.bundle 已验证包含下载仓库的完整可达历史。
这不是丢失的旧本地 Git 对象库的备份。

## 恢复结果

按内容而非修改时间比较：恢复 187 个修改文件、474 个新增文件，删除
1 个旧文件。此计数不含本恢复报告。1,076 个选定快照文件逐个核验通过；
唯一有意的内容差异是 pylock.toml 中本地包目录从临时目录名改回 `.`。
依赖版本沿用快照，不为迁移重新升级。

快照缺失但旧保存索引仍记录的四个文件，其 blob 与下载基线一致，保留：

- apps/web/pnpm-workspace.yaml
- apps/web/src/App.tsx
- apps/web/src/hooks/useWorkspaceRealtime.ts
- src/ai_kp/human_kp/control.py

移除 src/ai_kp/application/parallel_action_settlement_service.py：后续保存
索引已无此文件，恢复源码无引用；可从下载基线 Git 历史恢复。
不按“快照里没有”对其他文件作盲目删除。

未合入 Finder 元数据、打包 egg-info、测试环境 .env、缓存、node_modules、
测试产物或快照数据库。前端依赖已按锁文件离线安装到新目录；私密后端
配置仍单独保存在备份内，不自动启用、不提交。完整逐文件清单保存在
备份目录 recovery-final-manifest.json，包含恢复内容的 SHA-256。

## 在实际恢复目录运行的验证

- 后端：2,452 passed，1 skipped，53 subtests passed（280.58 秒）。
- 前端：49 个测试文件、252 项组件 / 单元测试通过。
- TypeScript 和 Vite 生产构建通过；存在超过 500 kB chunk 的非阻断警告。
- Ruff 检查 src / tests / scripts 通过；git diff --check 通过。

后端命令使用 /tmp/aikp-test-venv 的 Python，pytest 以恢复仓库的 src / tests
为源；并未在旧源码副本测试。永久后端 .venv 尚未新建，按 README 安装即可。
前端首轮默认并发测试退出 137，未据此判定业务失败；使用 bundled Node、
单 worker 重跑全部 src 测试通过。没有为通过测试修改业务断言。

复验命令（在 ai-kp-local 目录，依赖准备好后）：

```sh
python -m pytest -q
ruff check --no-cache src tests scripts
git diff --check
cd apps/web
pnpm build
pnpm exec vitest run src --maxWorkers=1 --minWorkers=1
```

## 尚不能宣称恢复的部分

- 原路径 Documents/跑团 已不存在，不能再据此核对原目录独有文件。
- 原始剧本、真实存档、原 Git 未推送提交对象，以及其他任务的独有改动，
  未证明全部包含在快照里。保存的旧索引与 main 引用不等于完整 Git 历史。
- 本轮不进行新功能开发、真实模型跑团或完整 UI 验收；此前 PRD 缺口仍在。
- 未自动 commit、push 或删除任何永久备份。恢复分支的变更仍待审阅提交。
