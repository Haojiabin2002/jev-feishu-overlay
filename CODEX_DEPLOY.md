# 本机部署与验收

公开仓库不包含真实配置或授权。保留已有飞书 CLI、登录、应用绑定与权限；不覆盖其他服务。正式入口为 AppKit 悬浮窗，旧 `run`、`bind` 和 `scripts/launchd.py` 不用于部署原生界面。

## 准备

按 README 运行 `scripts/bootstrap_native.sh`。脚本只创建缺少的配置模板，并安装项目依赖，不打开窗口或配置登录项。核实本机 Python、Node、Codex、飞书 CLI 和 Xcode Command Line Tools。原生后台测试依赖 Mac 的 AppKit。

在 `config.toml` 填入经确认的身份、唯一来源、程序路径和模型。`display_name` 可填写飞书显示名称，与无空白的本地 `alias` 分开。默认禁用所有示例来源，不通过扩大权限解决单一读取错误。

使用 `.venv/bin/python scripts/set_secrets.py --jev-only` 隐藏输入 TypeSafe Key，继续使用本机已有的 Codex 登录。不要把密钥粘贴进聊天、源码或 Issue。真实模型调用前必须取得对应来源、文本范围和模型目的地的同意。

## 分别验证

1. `doctor --overlay --live` 核实已有 CLI 契约、用户身份、Node/Codex 路径；不以此认定模型成功。
2. 用合成文本单独验证真实 Jev 判断、Codex 生成和 Jev 排序；真实失败不得替换为 demo。
3. 用一个明确授权的来源验证真实读取，最多 30 条原始记录。系统通知过滤后不足 30 条，不补拉更早数据。
4. 仅在取得模型处理同意后，提交该来源的文本做真实分析。持续授权只设置对应来源的 `allow_cloud=true`，不默认扩大为全局授权或自动分析。
5. 在用户方便时验证真正悬浮窗：首屏原消息和复制、编辑与读回、8 项判断、阶段状态、记录往返、来源切换、暂停与迟到结果隔离。合成截图和隐藏控件不能代替鼠标与系统剪贴板验证。

如需独立体验，使用 `scripts/prepare_blind_review.py` 导出中性任务和离线界面，按 `docs/BLIND_REVIEW_ORCHESTRATOR.md` 执行。不要把原始评审截图或私人报告提交到公开仓库。

## 可选能力

消息下方标注默认关闭，辅助功能有权限仍不保证客户端返回可用坐标。必须核实对应消息、位置、滚动隐藏、遮挡和点击；无法定位时保留记录菜单。

整窗跟随、Mac 输入框填入及登录启动分别验收，不用初始停靠或只读坐标代替动态跟随。输入只允许明确确认的可写空框，并需读回；不支持时只保留复制。不能覆盖现有草稿，不自动发送。

`scripts/make_app.py` 创建的 `.app` 引用当前源码和 `.venv`，并非独立安装包。只有用户明确确认后，才可使用 `scripts/launchd_overlay.py` 配置本项目登录项。

## 报告

将 `docs/DEPLOYMENT_REPORT.template.md` 复制为被 Git 忽略的 `DEPLOYMENT_REPORT.local.md`，分开记录离线测试、本机真实读取、真实模型、前台、独立评审和可选能力。只记录状态与必要统计，避免密钥、真实会话名、身份和正文。公开发布只能使用匿名汇总。
