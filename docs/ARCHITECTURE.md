# 架构

`overlay/native_app.py` 创建 AppKit NSPanel，复用 `vendor/jev_jarvis/native_ui.py` 的原生控件与布局。前台只展示和处理用户操作，模型工作在后台线程；epoch、取消信号和来源键阻止迟到结果被采用。

`overlay/feishu_provider.py` 通过 `sdk/feishu-read.mjs` 调用保留的 work-with-jev provider。项目注入只读 CLI runner，以现有 user 身份请求唯一来源的消息，最多读取配置上限，不为凑足文本额外翻页。

`messages.py` 归一化消息、区分参与者、限制上下文并脱敏；`questions.py` 定义固定判断问题、选项和评分规则；`models.py` 依次协调 Jev 判断、生成候选与 Jev 排序。Jev 不自行创建新的问题体系，生成模型不驱动应用操作。

`codex_backend.py` 和 `sdk/generate.mjs` 使用临时工作目录和一次性 SDK 请求，正文走 stdin，不继承 Jev 密钥。工具调用事件、未知错误或不完整结果会失败，不能回退为合成回答。

`overlay/history.py` 在界面采用结果前追加本机记录，按稳定来源键隔离。历史只读，个人编辑稿与模型原稿分开；不把旧历史恢复为可直接采用的新建议。

`native/MessageAnchors.swift` 只读 AX，先检查输入焦点和会话标题，再匹配原消息 hash、几何、空位及遮挡；`overlay/message_annotations.py` 只在可靠位置绘制本机标注。它不改写飞书消息流。无可靠位置时保留记录菜单。

旧 Tk 界面和机器人兼容模块保留在源码中以支持历史回归；默认原生入口不运行机器人发送逻辑。
