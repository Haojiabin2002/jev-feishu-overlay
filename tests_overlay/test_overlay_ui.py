from __future__ import annotations
import os
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
import unittest
import tkinter as tk
from jev_feishu.config import Config, Source
from jev_feishu.overlay.engine import Outcome, demo_result
from jev_feishu.overlay.input_bridge import FillResult
from jev_feishu.overlay.settings import OverlaySettings
from jev_feishu.overlay.ui import OverlayApp
from jev_feishu.overlay.viewmodel import replies, all_decisions

GUI_AVAILABLE = bool(os.environ.get('DISPLAY')) or sys.platform=='darwin'

class FakeBridge:
    def __init__(self):self.calls=[]
    def available(self):return True
    def fill(self,text,*,confirmed):self.calls.append(text);return FillResult('filled','已填入',True)

class FakeEngine:
    def __init__(self,base):self.base=base;self.calls=0
    def run(self,**kw):
        self.calls+=1;emit=kw['on_event'];r=demo_result(self.base);r.demo=False
        emit('capture','running',None);emit('capture','done',None)
        emit('judgement','running',replace(r,judgement={},candidates=[],ranking={}))
        emit('candidates','running',None)
        emit('judgement','done',replace(r,candidates=[],ranking={}))
        emit('candidates','done',replace(r,ranking={}))
        emit('ranking','running',replace(r,ranking={}))
        emit('ranking','done',r)
        return Outcome('sig','同事',r,read_at=time.time())

@unittest.skipUnless(GUI_AVAILABLE,'Requires a real Tk display; use xvfb-run on Linux')
class UITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
        self.cfg=Config(base=self.base,owner_open_id='ou_owner_test',allow_cloud=True,sources=(Source('同事',chat_id='oc_testgroup',enabled=True),Source('关闭',chat_id='oc_disabled',enabled=False)))
        self.root=tk.Tk();self.bridge=FakeBridge();self.engine=FakeEngine(self.base)
        self.app=OverlayApp(self.root,self.cfg,OverlaySettings(),engine=self.engine,bridge=self.bridge)
        self.errors=[];self.root.report_callback_exception=lambda *args:self.errors.append(args)
        self.env=patch.dict(os.environ,{'JEV_API_KEY':'test','LLM_API_KEY':'test'});self.env.start()
        self.pump(.04)
    def tearDown(self):
        self.app.close();self.env.stop();self.temp.cleanup()
        self.assertFalse(self.errors,'Tk callbacks must not error')
    def pump(self,seconds=.15):
        until=time.monotonic()+seconds
        while time.monotonic()<until:self.root.update();time.sleep(.003)
    def ready_result(self,live=False):
        self.app.load_demo();self.pump(.02)
        if live:
            self.app.demo=False;self.app.alias.set('同事');self.app.result_alias='同事';self.app.result.demo=False;self.app.result_at=time.time();self.app._render()
        return replies(self.app.result,self.cfg.minimum_grounding)[0]
    def test_default_no_auto_requests(self):
        self.pump(.2);self.assertFalse(self.app.auto.get());self.assertEqual(self.engine.calls,0)
    def test_selector_only_enabled_sources(self):
        self.assertEqual(tuple(self.app.combo['values']),('手动文本','同事'))
    def test_demo_has_three_cards_all_eight_decisions(self):
        self.ready_result();self.assertEqual(len(self.app.copy_buttons),1)
        self.app.toggle_alternatives();self.pump(.02);self.assertEqual(len(self.app.copy_buttons),3)
        self.app.select_tab('全部决策');self.pump(.04)
        def labels(w):
            values=[str(w['text'])] if isinstance(w,tk.Label) else []
            for child in w.winfo_children():values.extend(labels(child))
            return values
        shown=labels(self.app.area.body)
        self.assertEqual(len(all_decisions(self.app.result)),8)
        for d in all_decisions(self.app.result):self.assertIn(d.title,shown)
    def test_noul_label_no_separate_confidence(self):
        self.ready_result();self.app.select_tab('全部决策');self.pump(.02)
        def texts(w):
            result=[]
            if isinstance(w,tk.Label):result.append(w['text'])
            for c in w.winfo_children():result.extend(texts(c))
            return result
        alltext='\n'.join(texts(self.app.area.body))
        self.assertIn('没有单独的置信度',alltext)
    def test_copy_real_clipboard(self):
        r=self.ready_result();self.app.copy_reply(r)
        self.assertEqual(self.root.clipboard_get(),r.text);self.assertEqual(self.app.action_states[r.key],'已复制')
        self.assertNotIn('已发送',self.app.action_states[r.key])
    def test_busy_copy_is_disabled(self):
        r=self.ready_result(live=True);self.app.busy=True;self.app._render()
        self.assertTrue(self.app.copy_buttons[r.key].instate(['disabled']))
    def test_busy_copy_callback_cannot_adopt_stale_draft(self):
        r=self.ready_result(live=True);self.app.busy=True;self.app.copy_reply(r)
        self.assertNotIn(r.key,self.app.action_states)
    def test_demo_fill_disabled(self):
        r=self.ready_result();self.assertTrue(self.app.fill_buttons[r.key].instate(['disabled']))
        self.app.fill_reply(r);self.assertEqual(self.bridge.calls,[])
    def test_low_grounding_copy_disabled(self):
        self.ready_result();self.app.result.ranking['grounded_2']['noul']=.1;self.app._render()
        self.assertTrue(self.app.copy_buttons['reply_2'].instate(['disabled']))
    def test_none_fill_disabled(self):
        self.ready_result(live=True);self.app.result.ranking['best_reply']['choice']='none';self.app._render()
        self.assertTrue(all(b.instate(['disabled']) for b in self.app.fill_buttons.values()))
    def test_source_change_discards_result(self):
        self.ready_result();self.app.alias.set('同事');self.app._source_changed()
        self.assertIsNone(self.app.result);self.assertFalse(self.app.auto.get())
    def test_old_snapshot_not_displayed_after_source_switch(self):
        r=demo_result(self.base);old=self.app.epoch
        self.app.alias.set('同事');self.app._source_changed()
        self.app.events.put((old,'outcome',Outcome('sig','旧会话',r)))
        self.app._drain();self.assertIsNone(self.app.result)
    def test_old_finished_cannot_stop_new_worker(self):
        self.app.epoch=4;self.app.worker_epoch=4;self.app.busy=True
        self.app.events.put((3,'finished',None));self.app._drain()
        self.assertTrue(self.app.busy)
    def test_worker_progress_real_dispatch(self):
        self.app.alias.set('同事');self.app._source_changed();self.app.analyze()
        self.pump(.6)
        self.assertFalse(self.app.busy);self.assertEqual(self.app.progress.succeeded,4)
        self.assertIsNotNone(self.app.result);self.assertFalse(self.app.result.demo)
        self.assertEqual(self.app.result_alias,'同事')
    def test_ui_retains_both_copy_and_fill(self):
        r=self.ready_result(live=True);self.assertFalse(self.app.fill_buttons[r.key].instate(['disabled']))
    def test_fill_cancelled_before_helper(self):
        r=self.ready_result(live=True)
        with patch('jev_feishu.overlay.ui.messagebox.askokcancel',return_value=True):self.app.fill_reply(r)
        self.assertTrue(self.app.filling);self.app.pause();self.pump(.06)
        self.assertFalse(self.app.filling);self.assertEqual(self.bridge.calls,[])
        self.assertIn('未填入',self.app.action_states[r.key])
    def test_fill_declined(self):
        r=self.ready_result(live=True)
        with patch('jev_feishu.overlay.ui.messagebox.askokcancel',return_value=False):self.app.fill_reply(r)
        self.assertFalse(self.app.filling);self.assertEqual(self.bridge.calls,[])
    def test_fill_stale_result_refused(self):
        r=self.ready_result(live=True);self.app.result_at=time.time()-130;self.app.fill_reply(r)
        self.assertFalse(self.app.filling);self.assertIn('两分钟',self.app.notice)
    def test_started_fill_cannot_be_cancelled_as_unwritten(self):
        self.ready_result(live=True);self.app.filling=True;self.app.fill_started=True
        self.app.pause();self.assertTrue(self.app.filling);self.assertIn('不能撤回',self.app.notice)
        self.app.filling=False;self.app.fill_started=False
    def test_verified_fill_only_status(self):
        r=self.ready_result(live=True);self.app.filling=True
        self.app.events.put((self.app.epoch,'fill_result',(r.key,FillResult('filled','已填入',True))))
        self.app._drain()
        self.assertIn('读回确认',self.app.action_states[r.key]);self.assertIn('未发送',self.app.action_states[r.key])
    def test_unknown_fill_not_success(self):
        r=self.ready_result(live=True)
        self.app.events.put((self.app.epoch,'fill_result',(r.key,FillResult('unknown','未知'))));self.app._drain()
        self.assertEqual(self.app.action_states[r.key],'填入结果未确认')
    def test_collapse_restore(self):
        self.ready_result();self.app.toggle_collapse();self.pump(.02)
        self.assertTrue(self.app.collapsed);self.assertLess(self.root.winfo_width(),150)
        self.app.toggle_collapse();self.pump(.02);self.assertGreaterEqual(self.root.winfo_width(),440)
    def test_collapse_focus_is_moved_to_keyboard_operable_bubble(self):
        self.app.analyze_button.focus_force();self.pump(.02)
        self.app.toggle_collapse();self.pump(.02)
        self.assertEqual(self.root.focus_get(),self.app.bubble_text)
        self.app.bubble_text.event_generate('<space>');self.pump(.02)
        self.assertFalse(self.app.collapsed)
        self.assertEqual(self.root.focus_get(),self.app.analyze_button)
    def test_pause_preserves_old_judgement_as_not_execution(self):
        self.ready_result(live=True);self.app.pause()
        self.assertFalse(self.app.auto.get());self.assertIsNotNone(self.app.result)
        self.assertFalse(self.app.action_states)
    def test_error_clears_stale_drafts(self):
        self.ready_result(live=True)
        self.app.events.put((self.app.epoch,'error','source failure'));self.app._drain()
        self.assertIsNone(self.app.result);self.assertFalse(self.app.auto.get())

if __name__=='__main__':unittest.main()
