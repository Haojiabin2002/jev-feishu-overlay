# 上游复用

## 原生界面

来源：`jev-chat/jev-chat-jarvis-mac`，commit `1ac2d5f169c3be85080765bc91e7e00cec1d128f`，MIT。

`vendor/jev_jarvis/native_ui.py` 提取并复用 15 个界面、布局、原生按钮、收起和定位方法。`launcher.c` 原样复用，使 `.app` 父进程在 Python 运行期间存活。公开副本只携带使用的提取代码、启动器与许可证；完整上游文件可从上述 commit 取得。

适配为 440 点宽、一组 3 条候选和原生滚动视图。第二个候选动作改为“修改”，保留 Jev 原有 0–4 评分。布局先限制高度，尺寸相同时跳过窗口调整，避免反复增高缩小。没有导入微信 OCR、共享凭据、本地模型回退或非空输入框追加逻辑。

## 飞书接入

来源：`Adkid-Zephyr/work-with-jev`，commit `8d2db18ef753cd84e06a75dcaa6fa881b35c5a77`，MIT。

保留其 `lib/providers/feishu.mjs` 和 `lib/core.mjs`。注入的 runner 使用已有 lark-cli，以 user 身份读取唯一配置来源，通过原始 GET 接口保留毫秒时间和富文本消息数据。最多读取 30 条原始记录，系统通知过滤后可以少于 30 条。

飞书专用测试和核心测试在 `sdk/` 内，导入本仓库实际复用文件。未包含或声称通过上游其他平台和完整应用的全部测试。

精确来源、改动范围和版权见 `vendor/SOURCES.json`、各目录 LICENSE 与 `THIRD_PARTY_NOTICES.md`。
