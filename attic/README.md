# 历史实验面

这里保存 v1 的 web/export、agent/upper/skills/mcp、probe/probe-c2 家族、实验 API 契约、资产、脚本和测试。
不参与主构建、ruff 或 pytest；移动后的历史代码不保证可直接运行。contracts 不是 v1 domain schema 的事实源。
保留模块位于原路径；v1 schema 与保留 shell/bench 内容未改。

恢复时在独立分支/工作区运行 `git checkout v1-final -- <原路径>`，或 revert 归档提交及依赖断开/配置提交。
例如恢复 `molt/agent` 时还须恢复 contracts、相关资产、CLI 入口及测试/构建配置，不能只恢复单文件。
完整运行历史版本推荐 `git worktree add /tmp/molt-v1 v1-final`，避免新旧配置混合。
恢复不得覆盖用户未提交文件；历史展示数据不得被当作新 live 证据。
