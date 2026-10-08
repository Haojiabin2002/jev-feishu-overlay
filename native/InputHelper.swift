// Jev Feishu Overlay: explicit, one-shot AX value insertion; NEVER sends key events.
// No shell/clipboard/return-key fallback. See docs/INPUT_SAFETY.md.
import Foundation
#if os(macOS)
import AppKit
import ApplicationServices
import CoreFoundation

func output(_ obj: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys]),
       let line = String(data: data, encoding: .utf8) { print(line) }
}
func fail(_ reason: String) -> Never {
    output(["status": "blocked", "reason": reason, "verified": false]); exit(2)
}
func attribute(_ element: AXUIElement, _ name: String) -> CFTypeRef? {
    var value: CFTypeRef?
    guard AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success else { return nil }
    return value
}
func axElement(_ value: CFTypeRef?) -> AXUIElement? {
    guard let value = value, CFGetTypeID(value) == AXUIElementGetTypeID() else { return nil }
    return (value as! AXUIElement)
}
func stringAttribute(_ element: AXUIElement, _ name: String) -> String? {
    return attribute(element, name) as? String
}
let bytes = FileHandle.standardInput.readData(ofLength: 8193)
guard bytes.count <= 8192,
      let body = try? JSONSerialization.jsonObject(with: bytes) as? [String: Any],
      let action = body["action"] as? String else { fail("invalid_request") }
if action == "probe" {
    output(["status": "probe", "trusted": AXIsProcessTrusted(), "platform": "macOS"]); exit(0)
}
guard action == "fill", body["confirmed"] as? Bool == true,
      let allowed = body["allowed_bundle_ids"] as? [String], !allowed.isEmpty,
      let text = body["text"] as? String, !text.isEmpty, text.count <= 500,
      !text.contains("\u{0000}") else { fail("invalid_request") }
guard AXIsProcessTrusted() else { fail("accessibility_not_authorized") }
guard let app = NSWorkspace.shared.frontmostApplication,
      let bundleID = app.bundleIdentifier, allowed.contains(bundleID) else { fail("wrong_frontmost_app") }
let pid = app.processIdentifier
let appAX = AXUIElementCreateApplication(pid)
AXUIElementSetMessagingTimeout(appAX, 1.0)
guard let target = axElement(attribute(appAX, "AXFocusedUIElement")),
      let window = axElement(attribute(target, "AXWindow")) else { fail("no_focused_composer") }
AXUIElementSetMessagingTimeout(target, 1.0)
// Single-line AXTextField and search fields are deliberately refused.
guard stringAttribute(target, "AXRole") == "AXTextArea" else { fail("not_text_area") }
let desc = ((stringAttribute(target, "AXDescription") ?? "") + " " + (stringAttribute(target, "AXPlaceholderValue") ?? "")).lowercased()
guard !desc.contains("search"), !desc.contains("搜索"), !desc.contains("查找") else { fail("search_field") }
guard attribute(target, "AXEnabled") as? Bool == true else { fail("not_enabled") }
guard let existing = stringAttribute(target, "AXValue"), existing.isEmpty else { fail("nonempty_or_unreadable") }
var settable = DarwinBoolean(false)
guard AXUIElementIsAttributeSettable(target, "AXValue" as CFString, &settable) == .success,
      settable.boolValue else { fail("not_settable") }
let windowTitle = stringAttribute(window, "AXTitle") ?? ""
// Re-check focus and empty value immediately before the only mutation.
guard NSWorkspace.shared.frontmostApplication?.processIdentifier == pid,
      let current = axElement(attribute(appAX, "AXFocusedUIElement")), CFEqual(current, target),
      let currentWindow = axElement(attribute(current, "AXWindow")), CFEqual(currentWindow, window),
      (stringAttribute(window, "AXTitle") ?? "") == windowTitle,
      stringAttribute(target, "AXValue") == "" else { fail("focus_changed") }
let result = AXUIElementSetAttributeValue(target, "AXValue" as CFString, text as CFString)
// A failed write may still have had a side effect. Never blindly retry it.
if result != .success {
    output(["status":"unknown", "reason":"write_not_confirmed", "verified":false]); exit(3)
}
var matches = false
for _ in 0..<8 {
    if stringAttribute(target, "AXValue") == text { matches = true; break }
    Thread.sleep(forTimeInterval: 0.05)
}
if matches {
    output(["status":"filled", "verified":true, "sent":false]); exit(0)
}
output(["status":"unknown", "reason":"readback_mismatch", "verified":false]); exit(3)
#else
print("{\"status\":\"blocked\",\"reason\":\"macos_only\",\"verified\":false}")
exit(2)
#endif
