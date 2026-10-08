# Changelog

## 0.3.1 — public source snapshot

- Default frontend uses a native AppKit panel and reuses the specified upstream UI and Feishu provider.
- Supports source-scoped model consent, TypeSafe Jev plus Codex SDK, editable copy-only replies, and persistent local analysis history.
- Separates eight typed model decisions from actual execution status; stale results cannot be adopted after source changes or pause.
- Removes global keyboard monitoring, suspends message-position scanning during text input, and avoids redundant window resizing.
- Reports unavailable Codex executables and recovers message display after a failed read.
- Experimental native message annotations fail closed when source, text, geometry, free space, or occlusion cannot be verified.
- Public distribution excludes private configuration, conversations, records, logs, screenshots, binaries, and historical deployment materials. Synthetic previews are regenerated.

Known limitations are listed in README and VERIFICATION; this source snapshot is not a signed standalone macOS release.
