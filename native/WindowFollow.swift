// Read only window geometry. Never reads message contents, sets AX values or sends keys.
import Foundation
import AppKit
import ApplicationServices
import CoreFoundation

func attribute(_ element: AXUIElement, _ key: String) -> CFTypeRef? {
    var value: CFTypeRef?
    guard AXUIElementCopyAttributeValue(element, key as CFString, &value) == .success else { return nil }
    return value
}
func element(_ value: CFTypeRef?) -> AXUIElement? {
    guard let value, CFGetTypeID(value) == AXUIElementGetTypeID() else { return nil }
    return (value as! AXUIElement)
}
func geometry(bundle: String) -> [String: Any] {
    guard AXIsProcessTrusted() else { return ["status":"unavailable","reason":"accessibility"] }
    guard let app = NSRunningApplication.runningApplications(withBundleIdentifier: bundle).first,
          !app.isHidden else { return ["status":"unavailable","reason":"not_visible"] }
    let appAX = AXUIElementCreateApplication(app.processIdentifier)
    AXUIElementSetMessagingTimeout(appAX, 0.4)
    guard let window = element(attribute(appAX,"AXFocusedWindow")) ?? element(attribute(appAX,"AXMainWindow")),
          attribute(window,"AXMinimized") as? Bool != true,
          let pos = attribute(window,"AXPosition"), CFGetTypeID(pos) == AXValueGetTypeID(),
          let size = attribute(window,"AXSize"), CFGetTypeID(size) == AXValueGetTypeID() else {
        return ["status":"unavailable","reason":"no_window"]
    }
    var point = CGPoint.zero; var extent = CGSize.zero
    guard AXValueGetValue(pos as! AXValue, .cgPoint, &point),
          AXValueGetValue(size as! AXValue, .cgSize, &extent),
          extent.width > 250, extent.height > 200 else {
        return ["status":"unavailable","reason":"no_chat_window"]
    }
    return ["status":"window","x":point.x,"y":point.y,"width":extent.width,"height":extent.height]
}
let args = CommandLine.arguments
guard args.count >= 2, args[1].range(of: "^[A-Za-z0-9][A-Za-z0-9.-]{2,150}$", options:.regularExpression) != nil else { exit(2) }
let watch = args.contains("--watch")
let parent = getppid()
var previous = ""
repeat {
    autoreleasepool {
        let state = geometry(bundle:args[1])
        if let bytes = try? JSONSerialization.data(withJSONObject:state,options:.sortedKeys),
           let text = String(data:bytes,encoding:.utf8), text != previous {
            print(text); fflush(stdout); previous=text
        }
    }
    if watch { Thread.sleep(forTimeInterval:0.25) }
} while watch && getppid() == parent
