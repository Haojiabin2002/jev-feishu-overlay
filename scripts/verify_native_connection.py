#!/usr/bin/env python3
"""Read the configured selected Feishu source into hidden native controls. No models."""
from pathlib import Path
import sys,json,time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import AppKit
from jev_feishu.config import load_config
from jev_feishu.overlay.settings import load_settings
from jev_feishu.overlay.native_app import FeishuHudController
from jev_feishu.overlay.ux import focus_text

root=Path(__file__).resolve().parents[1]
cfg=load_config(root/'config.toml');settings=load_settings(root/'overlay.toml')
app=AppKit.NSApplication.sharedApplication();app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
c=FeishuHudController.alloc().init().configure(cfg,settings,headless=True)
c.refreshMessages_(None)
deadline=time.monotonic()+cfg.cli_timeout+12
while c.capture_busy and time.monotonic()<deadline:
    c.tick_(None);time.sleep(.03)
ok=c.result is not None and not c.result.demo and not c.capture_busy
report={'native_frontend':'reused AppKit NSPanel','success':ok,'source_count':1,
        'message_count':len(c.result.state['chat']['messages']) if ok else 0,
        'message_control_matches_capture':str(c.rows['message'].stringValue())==focus_text(c.result) if ok else False,
        'states':c.progress.states,'visible':bool(c.panel.isVisible()),'models_called':False,
        'message_content_logged':False,'error':c.last_error if not ok else ''}
c.closed=True;c.cancel.set();c.panel.close()
(root/'docs/tests/local').mkdir(parents=True,exist_ok=True)
(root/'docs/tests/local/native-live-connection.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps(report,ensure_ascii=False))
raise SystemExit(0 if ok else 1)
