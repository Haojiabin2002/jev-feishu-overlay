#!/usr/bin/env python3
"""Offline logic tests with withdrawn Tk windows and a private test clipboard.

This is NOT foreground layout, accessibility, or system clipboard acceptance.
"""
from pathlib import Path
import sys
import tkinter as tk
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tests_overlay.test_overlay_ui import UITests
from tests_overlay.test_ux_round import OfficeTaskTests, PresentationTests
from tests_overlay.test_single_page_hidden import HiddenSinglePageTests

VISUAL={
    'test_copy_button_fits_default_viewport','test_copy_button_fits_720px_window',
    'test_context_precedes_reply','test_editor_copy_control_actually_visible',
    'test_paste_analyze_control_actually_visible','test_settings_save_control_actually_visible',
    'test_context_close_control_actually_visible',
    'test_collapse_focus_is_moved_to_keyboard_operable_bubble',
}

def main():
    suite=unittest.TestSuite()
    suite.addTests(unittest.defaultTestLoader.discover(str(ROOT/'tests')))
    for pattern in ('test_overlay_core.py','test_delivery.py','test_compact.py','test_window_follow.py','test_live_capture.py'):
        suite.addTests(unittest.TestLoader().discover(str(ROOT/'tests_overlay'),pattern=pattern))
    for cls in (PresentationTests, HiddenSinglePageTests, UITests, OfficeTaskTests):
        for name in unittest.defaultTestLoader.getTestCaseNames(cls):
            if name not in VISUAL:suite.addTest(cls(name))
    original_tk=tk.Tk.__init__;original_top=tk.Toplevel.__init__
    def hidden_tk(self,*a,**kw):original_tk(self,*a,**kw);self.withdraw()
    def hidden_top(self,*a,**kw):original_top(self,*a,**kw);self.withdraw()
    clipboard=['']
    print('BACKGROUND LOGIC ONLY: all Tk windows withdrawn; system clipboard untouched.',flush=True)
    print(f'{len(VISUAL)} foreground visual checks deferred, not passed.',flush=True)
    with patch.object(tk.Tk,'__init__',hidden_tk),patch.object(tk.Toplevel,'__init__',hidden_top), \
         patch.object(tk.Misc,'clipboard_clear',lambda self:clipboard.__setitem__(0,'')), \
         patch.object(tk.Misc,'clipboard_append',lambda self,s:clipboard.__setitem__(0,clipboard[0]+s)), \
         patch.object(tk.Misc,'clipboard_get',lambda self:clipboard[0]):
        result=unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1

if __name__=='__main__':raise SystemExit(main())
