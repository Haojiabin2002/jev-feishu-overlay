# Jev 飞书对话助手

Mac 原生悬浮窗：通过已有的飞书 CLI 读取你明确选择的会话，由 TypeSafe Jev 判断、Codex SDK 拟写回复，再由 Jev 排序与核查。建议只显示在本机，由你修改、复制并决定是否发送。

![使用合成对话的原生界面](assets/offscreen-demo.png)

这是一份去除本机配置、凭据、真实会话和验收原始资料的源码发布。界面截图重新使用合成数据生成，不是实际群聊。

## 能做什么

- 原消息、判断和三条回复在一个 AppKit 窗口中展示；可展开原对话和完整的 8 项 Choice / Score / Noul 判断。
- 修改回复后重新核对再复制；修改稿不沿用原评分，不自动填入或发送。
- 每轮真实分析保存在本机记录中，按来源隔离；旧记录不会被下一轮覆盖，历史视图只读。
- 读取与模型调用分开。自动模型分析默认关闭，暂停或切换来源后旧结果不可直接采用。
- 复用现有飞书 CLI 的用户登录，不安装或换绑 CLI，不需要让机器人加入会话。

## 安装和配置

需要 macOS、Python 3.11+、Node.js 18+、Xcode Command Line Tools，以及已经登录并具有目标会话读取权限的 `lark-cli`。生成后端使用已有 Codex CLI / ChatGPT 登录，不要求额外的 OpenAI API Key。

```bash
git clone https://github.com/Haojiabin2002/jev-feishu-overlay.git
cd jev-feishu-overlay

# 仅安装本项目依赖、创建缺少的配置模板并执行后台检查。
# 不覆盖已有配置，不打开窗口，也不配置登录启动。
PYTHON_BIN=python3.11 bash scripts/bootstrap_native.sh
```

编辑本机 `config.toml`：

1. 将 `feishu.cli`、`models.node_binary`、`models.codex_binary` 设为本机可用的程序路径；可用 `command -v` 查询。桌面启动器建议使用绝对路径。
2. 用已有 CLI 核实自己的 `owner_open_id`，配置一个明确允许读取的会话 ID，并设 `enabled=true`。不要把示例 ID 当成真实 ID。
3. 默认使用 TypeSafe 官方 Jev 和 Codex SDK。模型 ID 需要你的账号支持；不支持时程序会报错，不会换模型或返回 demo。
4. 只在同意将该会话文本交给这两个模型服务后，设置对应来源的 `allow_cloud=true`；全局许可建议保持 `false`。也可在界面按当前来源确认本次使用许可。

```bash
# 在本机隐藏输入 Jev Key；不要写入源码、聊天或 Issue。
.venv/bin/python scripts/set_secrets.py --jev-only
chmod 600 config.toml overlay.toml .env

# 检查环境及已有飞书身份；不读取聊天、不调用模型、不发送消息。
.venv/bin/python -m jev_feishu --config config.toml doctor --overlay --live

# 打开正式原生悬浮窗
.venv/bin/python -m jev_feishu --config config.toml overlay
```

也可用 `.venv/bin/python scripts/make_app.py` 创建 `dist/Jev 飞书悬浮助手.app`。它引用当前源码和虚拟环境，不能单独移走当安装包使用，尚未签名或公证。

想先体验界面，可运行 `.venv/bin/python -m jev_feishu overlay --demo`。演示不连接飞书、不调用模型、不写真实记录，不能作为连接成功的证据。

## 日常使用

顶部“读取”只同步选定会话，“分析”执行模型链路；“自动分析”需要另外开启。工具不会根据你当前打开的飞书会话猜测读取来源。

找到合适的回复后，可直接复制，或点“修改”，核对后复制。程序会读回剪贴板再确认复制成功；到目标会话粘贴后，仍由你检查并决定发送。黄色按钮收起，浮条“展开”恢复，红色按钮退出。

“记录”按当前来源查看以前的原消息、模型原稿和阶段状态。“回当前”返回实时建议。记录文件在 `.state/analysis-history.sqlite3`，**其中包含经过现有脱敏处理的会话快照和模型结果，仍应视为私人数据**。

## 当前限制

- 消息下方本机标注是实验能力，默认关闭。需要当前飞书版本提供可靠的辅助功能坐标；无法核实原消息、留白或遮挡时隐藏，可从记录菜单回看。
- 整窗移动跟随默认关闭，尚不能保证所有窗口、缩放和多屏场景。
- 原生界面目前仅支持复制。Mac 自动填入尚未完成空框写入及读回验收；不通过快捷键粘贴或发送 API 兜底。
- 暂停会清除当前未复制的修改稿，需要保留时请先复制；历史保存模型原稿，不是个人编辑稿。
- Jev 的分布和置信度不是回复正确率或任务成功率。Score 保持 0–4，Noul 没有独立 confidence；当前接口不提供逐句证据解释。
- 登录启动不会自动配置；先完成本机验收，再决定是否使用可选脚本。

## 测试和隐私

```bash
.venv/bin/python scripts/test_background.py
.venv/bin/python -m unittest discover -s tests_native -v
node --test sdk/*.test.mjs
bash scripts/build_message_anchors.sh
native/jev-message-anchors --self-test
python3 scripts/check_public_repo.py
```

后台、原生离屏、真正的前台操作和真实模型是不同验证范围，见 [VERIFICATION.md](VERIFICATION.md)。发布的默认配置不会授权任何真实会话，也不会自动调用模型。

隐私边界与发布排除项见 [docs/SECURITY.md](docs/SECURITY.md)；部署步骤见 [CODEX_DEPLOY.md](CODEX_DEPLOY.md)。不要把 `.env`、本机配置、数据库、日志或真实截图上传到仓库或 Issue。

## 复用与许可

项目采用 MIT 许可，保留上游版权声明：

- [jev-chat/jev-chat-jarvis-mac](https://github.com/jev-chat/jev-chat-jarvis-mac)：AppKit 原生控件、布局与 C 启动器。
- [Adkid-Zephyr/work-with-jev](https://github.com/Adkid-Zephyr/work-with-jev)：飞书 provider、消息处理核心及相应用例。
- [open-aiee/jev-chat-JARVIS](https://github.com/open-aiee/jev-chat-JARVIS)：类型化判断和排序设计。

精确 commit、适配范围及其他来源见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 和 [vendor/SOURCES.json](vendor/SOURCES.json)。本项目不是这些项目或飞书的官方发行版。
