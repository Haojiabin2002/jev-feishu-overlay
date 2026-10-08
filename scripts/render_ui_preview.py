#!/usr/bin/env python3
"""Capture actual Tk windows with synthetic input (not macOS/live API evidence).
Pillow is optional and used only by this documentation/test tool.
"""
from pathlib import Path
import json
import tempfile
import tkinter as tk
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from PIL import ImageGrab
from jev_feishu.config import Config
from jev_feishu.overlay.settings import OverlaySettings
from jev_feishu.overlay.ui import OverlayApp
from jev_feishu.overlay.viewmodel import replies

rootdir=Path(__file__).resolve().parent.parent
out=rootdir/'docs/preview/single-page';out.mkdir(parents=True,exist_ok=True)
root=tk.Tk()
app=OverlayApp(root,Config(base=rootdir),OverlaySettings(width=490,height=820))
root.geometry('490x820+20+20')

def pump():
    for _ in range(4):root.update();root.update_idletasks()

def save(name,window=None):
    window=window or root;pump()
    x,y=window.winfo_rootx(),window.winfo_rooty()
    ImageGrab.grab(bbox=(x,y,x+window.winfo_width(),y+window.winfo_height())).save(out/name)

def capture():
    save('overlay-empty.png')
    app.load_demo();pump();save('overlay-overview.png')
    r=replies(app.result,.7)[0];b=app.copy_buttons[r.key];view=root
    measure={'method':'实际 Tk 测量；非独立子agent、非真人试用',
             'window_height':root.winfo_height(),
             'viewport_bottom':view.winfo_rooty()+view.winfo_height(),
             'first_copy_bottom':b.winfo_rooty()+b.winfo_height(),
             'first_copy_visible':b.winfo_rooty()+b.winfo_height()<=view.winfo_rooty()+view.winfo_height()}
    (rootdir/'docs/tests/local/single-page-ui-observations.json').write_text(json.dumps(measure,ensure_ascii=False,indent=2))
    app.edit_reply(r);pump();save('overlay-edit.png');app.editor.destroy();app.editor=None;app._render()
    app.context_dialog();pump();save('overlay-context.png');app.context_dialog()
    app.toggle_alternatives();pump();app.area.canvas.yview_moveto(.25);save('overlay-replies.png')
    app.toggle_raw();app.area.canvas.yview_moveto(1);save('overlay-decisions.png');app.toggle_raw()
    app.toggle_runtime();app.area.canvas.yview_moveto(1);save('overlay-runtime.png')
    app.close()
root.after(300,capture);root.mainloop()
