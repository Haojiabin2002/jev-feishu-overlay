// Local read-only AX matching for message annotations. No AX writes or key events.
import Foundation
import AppKit
import ApplicationServices
import CoreFoundation
import CryptoKit

func attr(_ e: AXUIElement, _ key: String) -> CFTypeRef? {
    var v: CFTypeRef?
    return AXUIElementCopyAttributeValue(e,key as CFString,&v) == .success ? v : nil
}
func element(_ v: CFTypeRef?) -> AXUIElement? {
    guard let v, CFGetTypeID(v) == AXUIElementGetTypeID() else { return nil }
    return (v as! AXUIElement)
}
func string(_ e: AXUIElement,_ key: String) -> String { attr(e,key) as? String ?? "" }
func rect(_ e: AXUIElement) -> CGRect? {
    guard let p=attr(e,"AXPosition"),let s=attr(e,"AXSize"),
          CFGetTypeID(p)==AXValueGetTypeID(),CFGetTypeID(s)==AXValueGetTypeID() else { return nil }
    var point=CGPoint.zero;var size=CGSize.zero
    guard AXValueGetValue(p as! AXValue,.cgPoint,&point),AXValueGetValue(s as! AXValue,.cgSize,&size),
          size.width>0,size.height>0 else { return nil }
    return CGRect(origin:point,size:size)
}
func children(_ e: AXUIElement) -> [AXUIElement] { attr(e,"AXChildren") as? [AXUIElement] ?? [] }
func normal(_ s: String) -> String {
    let stripped=s.replacingOccurrences(of:"@[^\\s，。,：:；;！？!?]+\\s*",with:"",options:.regularExpression)
    return String(stripped.unicodeScalars.filter { !CharacterSet.whitespacesAndNewlines.contains($0) && ![0x200B,0x200C,0x200D,0xFEFF].contains($0.value) })
}
func digest(_ s:String)->String { SHA256.hash(data:Data(normal(s).utf8)).map { String(format:"%02x",$0) }.joined() }
func headerEqual(_ value:String,_ expected:String)->Bool {
    func clean(_ s:String)->String {
        String(s.unicodeScalars.filter { ![0x200B,0x200C,0x200D,0xFEFF].contains($0.value) }).trimmingCharacters(in:.whitespacesAndNewlines)
    }
    return !clean(expected).isEmpty && clean(value)==clean(expected)
}
func focusBlock(_ role:String?,_ editable:Bool=false)->String? {
    guard let role,!role.isEmpty else { return "focus_unavailable" }
    return editable || ["AXTextArea","AXTextField","AXSearchField","AXComboBox"].contains(role) ? "editing" : nil
}
func emit(_ x: [String:Any]) {
    if let data=try? JSONSerialization.data(withJSONObject:x,options:.sortedKeys),let s=String(data:data,encoding:.utf8) { print(s) }
}
if CommandLine.arguments.contains("--self-test") {
    precondition(headerEqual(" Team Demo ","Team Demo"))
    precondition(!headerEqual("TeamDemo","Team Demo"))
    precondition(!headerEqual("群@A","群@B"))
    precondition(digest("@_user_1 请核对记录。") == digest("@同事 请核对记录。"))
    precondition(focusBlock("AXTextArea")=="editing")
    precondition(focusBlock("AXTextField")=="editing")
    precondition(focusBlock("AXGroup",true)=="editing")
    precondition(focusBlock(nil)=="focus_unavailable")
    precondition(focusBlock("AXStaticText")==nil)
    emit(["status":"passed","checks":9]);exit(0)
}
let data=FileHandle.standardInput.readData(ofLength:262145)
guard data.count<=262144,let request=try? JSONSerialization.jsonObject(with:data) as? [String:Any],
      let bundle=request["bundle"] as? String,let title=request["chat_title"] as? String,
      bundle.range(of:"^[A-Za-z0-9][A-Za-z0-9.-]{2,150}$",options:.regularExpression) != nil,
      !title.isEmpty,title.count<=100 else { emit(["status":"unavailable","reason":"invalid_request"]);exit(2) }
guard AXIsProcessTrusted() else { emit(["status":"unavailable","reason":"accessibility"]);exit(0) }
guard let app=NSRunningApplication.runningApplications(withBundleIdentifier:bundle).first,!app.isHidden else {
    emit(["status":"unavailable","reason":"not_visible"]);exit(0)
}
let appAX=AXUIElementCreateApplication(app.processIdentifier);AXUIElementSetMessagingTimeout(appAX,0.15)
func currentFocusBlock()->String? {
    guard let focused=element(attr(appAX,"AXFocusedUIElement")) else { return "focus_unavailable" }
    // Query role only; never read AXValue/selected text of a focused editor.
    return focusBlock(string(focused,"AXRole"),attr(focused,"AXEditable") as? Bool ?? false)
}
if let reason=currentFocusBlock() { emit(["status":"unavailable","reason":reason]);exit(0) }
let deadline=Date().addingTimeInterval(4)
func chatArea(_ window:AXUIElement)->AXUIElement? {
    var queue=[window];var cursor=0
    while cursor<queue.count && cursor<2000 && Date()<deadline {
        let e=queue[cursor];cursor+=1
        if string(e,"AXRole")=="AXWebArea" {
            let url=(attr(e,"AXURL") as? URL)?.absoluteString ?? string(e,"AXURL")
            if url.contains("/messenger-chat/") { return e }
            continue
        }
        queue.append(contentsOf:children(e))
    }
    return nil
}
func matchesHeader(_ web:AXUIElement,_ bounds:CGRect)->Bool {
    // Read only header text until the explicitly selected conversation is verified.
    var queue=[web];var cursor=0
    while cursor<queue.count && cursor<2500 && Date()<deadline {
        let e=queue[cursor];cursor+=1
        if let r=rect(e),r.minY>bounds.minY+65 { continue }
        if string(e,"AXRole")=="AXStaticText",let r=rect(e),r.minY>=bounds.minY,r.maxY<=bounds.minY+65,
           headerEqual(string(e,"AXValue"),title) { return true }
        queue.append(contentsOf:children(e))
    }
    return false
}
var candidates=[AXUIElement]()
for e in [element(attr(appAX,"AXFocusedWindow")),element(attr(appAX,"AXMainWindow"))].compactMap({$0}) + (attr(appAX,"AXWindows") as? [AXUIElement] ?? []) {
    if !candidates.contains(where:{CFEqual($0,e)}) { candidates.append(e) }
}
var selected:(AXUIElement,CGRect,AXUIElement,CGRect)?;var sawChat=false
for window in candidates.prefix(10) {
    guard Date()<deadline,attr(window,"AXMinimized") as? Bool != true,let windowRect=rect(window),
          let web=chatArea(window),let webRect=rect(web) else { continue }
    sawChat=true
    if matchesHeader(web,webRect) { selected=(window,windowRect,web,webRect);break }
}
guard let (_,windowRect,web,webRect)=selected else {
    emit(["status":"unavailable","reason":sawChat ? "wrong_chat_or_header_unavailable" : "no_chat_area"]);exit(0)
}
let input=(request["anchors"] as? [[String:String]] ?? []).prefix(50)
let needles=Dictionary(input.compactMap { item -> (String,String)? in
    guard let key=item["key"],let hash=item["hash"],hash.range(of:"^[a-f0-9]{64}$",options:.regularExpression) != nil else { return nil }
    return (key,hash)
},uniquingKeysWith:{ a,_ in a })
var matches=[String:[CGRect]]();var missingGeometry=Set<String>();var leaves=[CGRect]();var count=0;var composerY=webRect.maxY
func visit(_ e:AXUIElement,_ depth:Int)->String {
    guard depth<45,count<4000,Date()<deadline else { return "" };count+=1
    let role=string(e,"AXRole");let bounds=rect(e)
    if role=="AXTextArea" || role=="AXTextField" {
        if let r=bounds,r.minY>webRect.midY { composerY=min(composerY,r.minY) };return ""
    }
    var text=""
    if role=="AXStaticText" {
        text=string(e,"AXValue")
        if let r=bounds,!text.isEmpty,r.intersects(webRect),r.minY>webRect.minY+70 { leaves.append(r) }
    } else {
        for child in children(e) { text+=" "+visit(child,depth+1);if text.count>15000 { return "" } }
    }
    if bounds==nil && normal(text).count>=6 {
        let value=digest(text)
        for (key,needle) in needles where value==needle { missingGeometry.insert(key) }
    }
    if let r=bounds,r.intersects(webRect),r.minY>webRect.minY+70,r.height<500 {
        if normal(text).count>=6 {
            let value=digest(text)
            for (key,needle) in needles where value==needle { matches[key,default:[]].append(r) }
        }
    }
    return text
}
_ = visit(web,0)
var anchors=[[String:Any]]();var rejected=[String:String]()
let windows=CGWindowListCopyWindowInfo(.optionOnScreenOnly,kCGNullWindowID) as? [[String:Any]] ?? []
func windowBounds(_ w:[String:Any])->CGRect? {
    guard let b=w[kCGWindowBounds as String] as? [String:CGFloat],let x=b["X"],let y=b["Y"],let width=b["Width"],let height=b["Height"] else { return nil }
    return CGRect(x:x,y:y,width:width,height:height)
}
let targetIndex=windows.firstIndex { w in
    guard (w[kCGWindowOwnerPID as String] as? Int)==Int(app.processIdentifier),
          (w[kCGWindowLayer as String] as? Int)==0,let b=windowBounds(w) else { return false }
    return abs(b.minX-windowRect.minX)<4 && abs(b.minY-windowRect.minY)<4 && abs(b.width-windowRect.width)<4
}
let ignored=Set(request["ignore_window_ids"] as? [Int] ?? [])
let overlayPID=request["overlay_pid"] as? Int ?? -1
let above=targetIndex.map { Array(windows.prefix($0)) } ?? windows
func unoccluded(_ r:CGRect)->Bool {
    guard targetIndex != nil else { return false }
    return !above.contains { w in
        if let id=w[kCGWindowNumber as String] as? Int,ignored.contains(id),
           (w[kCGWindowOwnerPID as String] as? Int)==overlayPID { return false }
        guard let b=windowBounds(w),(w[kCGWindowAlpha as String] as? Double ?? 1)>0.05 else { return false }
        return b.intersects(r)
    }
}
for (key,_) in needles {
    var clusters=[CGRect]()
    for r in (matches[key] ?? []).sorted(by: { $0.width*$0.height > $1.width*$1.height }) {
        if !clusters.contains(where: { $0.intersects(r) }) { clusters.append(r) }
    }
    guard clusters.count==1 else { rejected[key]=clusters.isEmpty ? "not_visible_or_not_exact" : "ambiguous";continue }
    let r=clusters[0]
    let next=leaves.filter { $0.minY>=r.maxY-1 && !$0.intersects(r) }.map(\.minY).min() ?? composerY
    let bottom=min(next,composerY,webRect.maxY)
    guard r.minY>=webRect.minY+70,r.maxY+26<=bottom else { rejected[key]="insufficient_gap";continue }
    let bar=CGRect(x:r.minX,y:r.maxY+2,width:min(400,r.width,webRect.maxX-r.minX-10),height:22)
    guard unoccluded(bar) else { rejected[key]="covered_by_another_window";continue }
    anchors.append(["key":key,"x":bar.minX,"y":bar.minY,"width":bar.width,"height":bar.height,"unoccluded":true,
                    "message_x":r.minX,"message_y":r.minY,"message_width":r.width,"message_height":r.height])
}
// Discard a scan if input focus moved into an editor while it was running.
if let reason=currentFocusBlock() { emit(["status":"unavailable","reason":reason]);exit(0) }
if anchors.isEmpty && !missingGeometry.isEmpty && Date()<deadline {
    emit(["status":"unavailable","reason":"飞书未提供原消息坐标，记录仍可查看","chat_verified":true,
          "matched_without_geometry":missingGeometry.count,"nodes":count,"anchors":[]]);exit(0)
}
emit(["status":"matched","chat_verified":true,"foreground":NSWorkspace.shared.frontmostApplication?.processIdentifier==app.processIdentifier,
      "occlusion_checked":targetIndex != nil,
      "visible_text_count":leaves.count,"nodes":count,"timed_out":Date()>=deadline,"anchors":anchors,"rejected":rejected,
      "window":["x":windowRect.minX,"y":windowRect.minY,"width":windowRect.width,"height":windowRect.height]])
