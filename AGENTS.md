# Project instructions

## Scope and entry point

This is a macOS AppKit reply assistant. The default entry point is `python -m jev_feishu --config config.toml overlay`; `overlay --demo` is synthetic and offline. The old Tk implementation and bot-compatible commands remain for regression and compatibility, but are not the native deployment path.

Read `CODEX_DEPLOY.md`, `VERIFICATION.md`, and `docs/SECURITY.md` before deployment. No real account, source ID, key, conversation, or user permission is included in this public checkout.

## Boundaries

- Preserve existing CLI installations, login bindings, permissions, and unrelated services. Read only explicitly configured sources using user identity.
- Never send Feishu messages from the overlay. Keep `allow_bot_send=false` and automatic analysis off by default.
- Cloud consent is source-specific. Demo is not a live fallback. Never silently change providers or models.
- Keep keys in local hidden input or environment; do not print them. Do not commit runtime data, reports, logs, screenshots of real conversations, or machine-specific paths.
- Choice probabilities and confidence remain distinct; Score stays 0–4; Noul has no independent confidence. Edited replies do not retain the original score or become automatically adopted.
- Copy success requires clipboard readback. Native filling remains disabled unless explicitly approved and verified against a writable empty input with readback; no keystroke, paste, Return, or send fallback.
- Do not monitor other apps' keyboard events. Suspend AX message scanning while an editor is focused. Do not resize the main window for transient annotation status or unchanged geometry.
- Message annotations require verified source, unique message text, usable geometry, free space, and occlusion checks. Hide when uncertain; never fake placement or modify Feishu.
- Login startup requires a separate explicit decision after real foreground acceptance.

## Implementation

Native UI: `jev_feishu/overlay/native_app.py`, reusing `vendor/jev_jarvis/native_ui.py`. Feishu bridge: `sdk/feishu-read.mjs` and `overlay/feishu_provider.py`. Async orchestration and stale-result guards: `overlay/engine.py`. Local private history: `overlay/history.py`. Read-only annotation matching: `native/MessageAnchors.swift` and `overlay/message_annotations.py`.

Run the tests relevant to changes, and distinguish hidden controls from actual GUI acceptance. Keep third-party licenses and source references. If independent UX review is requested, follow `docs/BLIND_REVIEW_ORCHESTRATOR.md`; never claim context isolation or real clicks without evidence.

Before publishing, run `scripts/check_public_repo.py`, scan the complete Git history for secrets, and inspect only the files intended for publication. This repository's permission defaults must remain disabled.
