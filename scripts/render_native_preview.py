#!/usr/bin/env python3
"""Render synthetic native AppKit controls while the panel remains offscreen."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import AppKit
from jev_feishu.config import Config
from jev_feishu.overlay.settings import OverlaySettings
from jev_feishu.overlay.native_app import FeishuHudController

root=Path(__file__).resolve().parents[1]
app=AppKit.NSApplication.sharedApplication();app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
controller=FeishuHudController.alloc().init().configure(Config(base=root),OverlaySettings(),demo=True,headless=True)
out=root/'assets';out.mkdir(parents=True,exist_ok=True)

def strip_metadata(data):
    """Remove ancillary text/EXIF metadata without changing image pixel chunks."""
    if data[:8]!=b'\x89PNG\r\n\x1a\n':raise ValueError('Expected PNG')
    cleaned=bytearray(data[:8]);offset=8
    while offset<len(data):
        length=int.from_bytes(data[offset:offset+4],'big');end=offset+length+12
        if end>len(data):raise ValueError('Invalid PNG chunk')
        kind=data[offset+4:offset+8]
        if kind not in (b'tEXt',b'zTXt',b'iTXt',b'eXIf'):cleaned.extend(data[offset:end])
        offset=end
    return bytes(cleaned)

def save(name):
    assert not controller.panel.isVisible(), 'Preview must never open a foreground window'
    view=controller._document;rect=view.bounds();rep=view.bitmapImageRepForCachingDisplayInRect_(rect)
    view.cacheDisplayInRect_toBitmapImageRep_(rect,rep)
    rep.representationUsingType_properties_(AppKit.NSPNGFileType,{}).writeToFile_atomically_(str(out/name),True)
    path=out/name;path.write_bytes(strip_metadata(path.read_bytes()))
    assert not controller.panel.isVisible()

save('offscreen-demo.png')
controller._rows[0][0]['fill_btn'].performClick_(None)
save('offscreen-edit.png')
controller.toggleDetails_(None)
save('offscreen-details.png')
controller.closed=True;controller.panel.close()
print('Three synthetic offscreen AppKit renders saved; no foreground or clipboard actions.')
