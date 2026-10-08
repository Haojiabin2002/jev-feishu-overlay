# Third-party provenance and licenses

Application code is MIT-licensed. Adapted code retains upstream notices and licenses. No credentials, model weights, fonts, system frameworks, CLI binaries, or commercial service access are included.

## Native AppKit UI and launcher

- Repository: [jev-chat/jev-chat-jarvis-mac](https://github.com/jev-chat/jev-chat-jarvis-mac)
- Commit: `1ac2d5f169c3be85080765bc91e7e00cec1d128f`
- Copyright (c) 2026 eatmoreduck; [MIT license](vendor/jev_jarvis/LICENSE).
- Reused: 15 native UI/layout/window methods extracted into `native_ui.py`, plus the unchanged C launcher.
- Adaptations: compact reply layout, source selection, editing, scrolling, bounded/idempotent window resizing. No upstream shared model credentials, OCR, local-model fallback, or append-to-composer behavior is imported.

## Feishu provider and message core

- Repository: [Adkid-Zephyr/work-with-jev](https://github.com/Adkid-Zephyr/work-with-jev)
- Commit: `8d2db18ef753cd84e06a75dcaa6fa881b35c5a77`
- Copyright (c) 2026 Adkid-Zephyr; [MIT license](vendor/work_with_jev/LICENSE).
- Reused: `lib/providers/feishu.mjs`, `lib/core.mjs`, adapted Feishu tests and core tests. Test import paths target the vendored files.
- Adaptation: the injected runner calls the existing user CLI and retains raw message timing/body data within the configured read limit. Other upstream platform providers are not bundled or tested here.

## Typed Jev judgement and ranking design

- Repository: [open-aiee/jev-chat-JARVIS](https://github.com/open-aiee/jev-chat-JARVIS)
- Commit: `37753bef127b6cd16983b401a6701033a58c585d`
- Copyright (c) 2026 Jev 聊天助手 contributors; [MIT license](upstream_snapshots/LICENSE.jev-chat-JARVIS).
- The question/ranking design and split judgement/generation workflow are adapted for workplace use. A reference excerpt is retained at `upstream_snapshots/jev_rank_original.py`. Calibration claims do not transfer to changed rubrics.

## Existing Feishu CLI

- Repository: [larksuite/cli](https://github.com/larksuite/cli)
- Inspected snapshot: `32d198896816e9416711468c20b14df3dbbc63f3`.
- Copyright (c) 2026 Lark Technologies Pte. Ltd.; [MIT license](upstream_snapshots/LICENSE.lark-cli).
- The application uses the user's installed executable. Request-shape logic is adapted from official source; no CLI executable or credential store is distributed.

## Runtime dependencies

Python, Tk, Apple AppKit/ApplicationServices, and Node are provided by the installation environment. PyObjC dependencies and `@openai/codex-sdk` are installed separately under their own licenses. Lockfiles describe those dependencies; they do not bundle them.

TypeSafe and generation API use is subject to the relevant provider's terms, account permissions, and source-data authorization. This is not an official release of Feishu, TypeSafe, OpenAI, or any upstream project.

Detailed source identifiers are preserved in `sources.lock.json` and `vendor/SOURCES.json`.
