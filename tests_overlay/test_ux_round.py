"""Task-based regression checks, not a claim of blind or human usability testing."""
from __future__ import annotations
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
import json
import os
import stat
import sys
import tempfile
import time
import tkinter as tk
from tkinter import ttk
import unittest

from jev_feishu.config import Config, Source
from jev_feishu.overlay.engine import demo_result
from jev_feishu.overlay.ui import OverlayApp
from jev_feishu.overlay.settings import OverlaySettings
from jev_feishu.overlay.preferences import load_preferences, save_preferences
from jev_feishu.overlay.ux import (readiness, friendly_error, friction_label,
                                  readable_context, focus_text, next_step_text)
from jev_feishu.overlay.viewmodel import replies, all_decisions


def descendants(w):
    yield w
    for child in w.winfo_children():yield from descendants(child)


def texts(w):
    return [str(x['text']) for x in descendants(w) if 'text' in x.keys()]


class PresentationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name)
        self.cfg=Config(base=self.base);self.result=demo_result(self.base)
    def tearDown(self):self.tmp.cleanup()
    def test_demo_does_not_spawn_model_threads(self):
        with patch('jev_feishu.models.ThreadPoolExecutor',side_effect=AssertionError('demo thread')):
            self.assertTrue(demo_result(self.base).demo)
    def test_missing_models_are_distinct(self):
        with patch.dict(os.environ,{},clear=True):
            text=' '.join(readiness(self.cfg))
        self.assertIn('Jev',text);self.assertIn('LLM',text)
    def test_readiness_does_not_expose_secrets(self):
        with patch.dict(os.environ,{'JEV_API_KEY':'dont-print-a','LLM_API_KEY':'dont-print-b'}):
            text=str(readiness(replace(self.cfg,allow_cloud=True)))
        self.assertEqual(text,'[]');self.assertNotIn('dont-print',text)
    def test_readable_context_no_json_keys(self):
        text=readable_context(self.result,'示例')
        self.assertIn('明天下午',text);self.assertIn('本次主要回复',text)
        self.assertNotIn('focus_message_id',text);self.assertNotIn('participant_1',text);self.assertIn('发言者 1',text)
    def test_focus_comes_from_original_message(self):
        self.assertIn('明天下午能给我一个可演示版本吗',focus_text(self.result))
    def test_high_conflict_not_green(self):
        self.assertEqual(friction_label({'probabilities':{'0':0,'1':0,'2':0,'3':0,'4':1}})[1],'red')
    def test_uncertain_conflict_not_claimed_safe(self):
        word,tone=friction_label({'probabilities':{'0':.3,'1':.2,'2':.2,'3':.2,'4':.1}})
        self.assertEqual(tone,'amber');self.assertIn('分歧',word)
    def test_friendly_error_has_next_action(self):
        self.assertIn('连接 / 设置',friendly_error('缺少环境变量 JEV_API_KEY'))
    def test_unknown_errors_not_echoed(self):
        self.assertNotIn('SECRET',friendly_error('external payload SECRET=123'))
    def test_next_action_does_not_claim_execution(self):
        self.assertNotIn('已核查',next_step_text(self.result))
    def test_preferences_round_trip_only_cosmetics(self):
        save_preferences(self.base,.81,510,840)
        original=OverlaySettings(allow_fill=False)
        loaded=load_preferences(self.base,original)
        self.assertEqual(loaded.opacity,.81);self.assertFalse(loaded.allow_fill)
    def test_preferences_cannot_enable_fill(self):
        p=self.base/'.state/overlay-ui.json';p.parent.mkdir()
        p.write_text('{"allow_fill":true,"opacity":0.8}')
        self.assertEqual(load_preferences(self.base,OverlaySettings()),OverlaySettings())
    def test_invalid_cosmetic_values_dont_break_startup(self):
        p=self.base/'.state/overlay-ui.json';p.parent.mkdir();p.write_text('{"width":1}')
        self.assertEqual(load_preferences(self.base,OverlaySettings()),OverlaySettings())
    def test_ui_preferences_private_permissions(self):
        save_preferences(self.base,.9,500,800)
        if os.name=='posix':self.assertEqual(stat.S_IMODE((self.base/'.state/overlay-ui.json').stat().st_mode),0o600)
    def test_ui_preferences_dont_modify_config(self):
        p=self.base/'overlay.toml';p.write_text('untouched')
        save_preferences(self.base,.9,500,800)
        self.assertEqual(p.read_text(),'untouched')
    def test_symlink_preferences_rejected(self):
        outside=self.base/'elsewhere';outside.mkdir()
        (self.base/'.state').symlink_to(outside,target_is_directory=True)
        with self.assertRaises(ValueError):save_preferences(self.base,.9,500,800)


GUI = bool(os.environ.get('DISPLAY')) or sys.platform=='darwin'
@unittest.skipUnless(GUI,'Requires actual GUI session; skip is not a pass')
class OfficeTaskTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name)
        self.cfg=Config(base=self.base,owner_open_id='ou_testowner',sources=(Source('项目讨论',chat_id='oc_testchat',enabled=True),))
        self.root=tk.Tk();self.app=OverlayApp(self.root,self.cfg,OverlaySettings())
        self.errors=[];self.root.report_callback_exception=lambda *a:self.errors.append(a)
        self.pump()
    def tearDown(self):
        self.app.close();self.tmp.cleanup();self.assertFalse(self.errors)
    def pump(self):
        for _ in range(4):self.root.update();time.sleep(.003)
    def ready(self):
        self.app.load_demo();self.pump()
        return replies(self.app.result,self.cfg.minimum_grounding)[0]
    def test_empty_state_gives_clear_first_action(self):
        ts=' '.join(texts(self.root))
        self.assertIn('粘贴对话',ts);self.assertIn('使用前还差这些',ts)
    def test_copy_button_fits_default_viewport(self):
        r=self.ready();button=self.app.copy_buttons[r.key];view=self.root
        self.assertLessEqual(button.winfo_rooty()+button.winfo_height(),view.winfo_rooty()+view.winfo_height())
    def test_copy_button_fits_720px_window(self):
        r=self.ready();self.root.geometry('490x720+10+10');self.pump()
        b=self.app.copy_buttons[r.key];view=self.root
        self.assertLessEqual(b.winfo_rooty()+b.winfo_height(),view.winfo_rooty()+view.winfo_height())
    def test_context_precedes_reply(self):
        self.ready()
        self.assertLess(self.app.focus_area.winfo_rooty(),self.app.reply_area.winfo_rooty())
    def test_alternatives_are_discoverable(self):
        self.ready();self.assertEqual(len(self.app.copy_buttons),1)
        self.app.toggle_alternatives();self.pump();self.assertEqual(len(self.app.copy_buttons),3)
    def test_all_original_decisions_remain(self):
        self.ready();self.app.select_tab('全部决策');self.pump()
        rows=all_decisions(self.app.result)
        self.assertEqual(len(rows),8);self.assertEqual({d.kind for d in rows},{'Choice','Score','Noul'})
        self.app.toggle_distribution();self.pump()
        ts=' '.join(texts(self.app.area.body))
        self.assertIn('Choice',ts);self.assertIn('没有单独的置信度',ts)
    def test_no_candidate_identity_mismatch_in_cards(self):
        self.ready();self.assertNotIn('#1',' '.join(texts(self.app.area.body)))
    def test_context_dialog_is_readable(self):
        self.ready();self.app.context_dialog();self.pump()
        content=' '.join(texts(self.app.area.body))
        self.assertIn('本次主要回复',content)
        self.assertNotIn('focus_message_id',content)
        self.assertFalse(any(isinstance(w,tk.Toplevel) for w in descendants(self.root)))
    def test_edit_copy_requires_confirmation(self):
        r=self.ready();self.app.edit_reply(r);self.pump()
        self.app.editor_copy();self.pump()
        self.assertNotIn(r.key,self.app.action_states)
    def test_edit_copy_works_without_model_or_fill(self):
        r=self.ready();self.app.edit_reply(r);self.pump()
        box=self.app.editor_text;box.delete('1.0','end');box.insert('1.0','演示要覆盖哪几个流程？')
        self.pump();self.app.editor_confirm.set(True);self.app.editor_copy();self.pump()
        self.assertEqual(self.root.clipboard_get(),'演示要覆盖哪几个流程？')
        self.assertIn('未重新核查',self.app.action_states[r.key]);self.assertFalse(self.app.filling)
    def test_edit_after_confirmation_resets_it(self):
        r=self.ready();self.app.edit_reply(r);self.pump();self.app.editor_confirm.set(True)
        self.app.editor_text.insert('end','新增');self.pump()
        self.assertFalse(self.app.editor_confirm.get())
    def test_source_switch_invalidates_open_editor(self):
        r=self.ready();self.app.edit_reply(r);self.pump()
        self.app.alias.set('项目讨论');self.app._source_changed()
        self.app.editor_confirm.set(True);self.app.editor_copy();self.pump()
        self.assertNotIn(r.key,self.app.action_states)
    def test_manual_edit_cannot_mutate_scored_draft(self):
        r=self.ready();before=list(self.app.result.candidates)
        self.assertTrue(self.app._copy_edited(r,'我确认后再回复。'))
        self.assertEqual(self.app.result.candidates,before)
    def test_empty_text_does_not_start_request(self):
        self.app.text_dialog();self.pump()
        b=next(w for w in descendants(self.app.text_editor) if isinstance(w,ttk.Button) and w['text']=='生成回复')
        b.invoke();self.pump()
        self.assertFalse(self.app.busy);self.assertIn('先粘贴至少一条消息。',' '.join(texts(self.app.text_editor)))
    def test_overlong_text_does_not_start_request(self):
        self.app.text_dialog();self.pump()
        box=next(w for w in descendants(self.app.text_editor) if isinstance(w,tk.Text));box.insert('1.0','字'*13000)
        b=next(w for w in descendants(self.app.text_editor) if isinstance(w,ttk.Button) and w['text']=='生成回复');b.invoke();self.pump()
        self.assertFalse(self.app.busy);self.assertIn('内容过长',' '.join(texts(self.app.text_editor)))
    def test_disabled_fill_explains_next_step(self):
        self.ready();self.assertIn('示例不能填入飞书，可以试用复制。',' '.join(texts(self.root)))
    def test_demo_has_explicit_exit(self):
        self.ready();self.app.exit_demo();self.pump()
        self.assertFalse(self.app.demo);self.assertIsNone(self.app.result)
    def test_idle_stop_is_disabled(self):
        self.assertTrue(self.app.stop_button.instate(['disabled']))
    def test_pausing_inflight_makes_existing_result_stale(self):
        r=self.ready();self.app.busy=True;self.app.pause();self.app.busy=False;self.app._render();self.pump()
        self.app.copy_reply(r)
        self.assertTrue(self.app.result_stale);self.assertNotIn(r.key,self.app.action_states)
    def test_preflight_failure_is_visible_with_action(self):
        self.app.text_input='我：进度没确定。\n同事：什么时候能给？';self.app.analyze();self.pump()
        self.assertTrue(self.app.last_error);self.assertFalse(self.app.busy)
        self.assertIn('检查连接',' '.join(texts(self.app.area.body)))
    def test_copy_success_plain_language(self):
        r=self.ready();self.app.copy_buttons[r.key].invoke();self.pump()
        self.assertIn('去飞书粘贴',self.app.notice);self.assertEqual(self.root.clipboard_get(),r.text)
    def test_source_mismatch_warning_is_always_visible(self):
        self.app.alias.set('项目讨论');self.app._source_changed();self.pump()
        self.assertIn('不跟随飞书切换聊天',self.app.source_line['text'])
    def test_setup_task_has_no_credentials(self):
        with patch.dict(os.environ,{'JEV_API_KEY':'NEVER_ECHO_THIS'}):self.app.copy_setup_task()
        self.assertNotIn('NEVER_ECHO_THIS',self.root.clipboard_get());self.assertIn('Codex',self.app.notice)
    def test_same_dialog_does_not_duplicate(self):
        self.app.text_dialog();self.app.text_dialog();self.pump()
        self.assertEqual(sum(isinstance(w,tk.Toplevel) for w in self.root.winfo_children()),1)

    def test_new_round_same_text_invalidates_editor(self):
        r=self.ready();self.app.edit_reply(r);self.pump()
        self.app.result_revision+=1
        self.app.editor_confirm.set(True);self.app.editor_copy();self.pump()
        self.assertNotIn(r.key,self.app.action_states)
    def test_none_recommendation_explains_without_implying_winner(self):
        self.ready();self.app.result.ranking['best_reply']['choice']='none';self.app._render();self.pump()
        self.assertIn('这轮没有推荐回复',' '.join(texts(self.root)))
        self.assertEqual(len(self.app.copy_buttons),0)
        self.app.toggle_alternatives();self.pump();self.assertEqual(len(self.app.copy_buttons),3)
    def test_setup_window_save_is_visible(self):
        self.app.settings_dialog();self.pump()
        self.assertIn('保存外观',' '.join(texts(self.app.settings_window)))

    def assert_control_visible(self,window,text):
        b=next(w for w in descendants(window) if 'text' in w.keys() and w['text']==text)
        self.assertTrue(b.winfo_ismapped())
        self.assertGreaterEqual(b.winfo_rooty(),window.winfo_rooty())
        self.assertLessEqual(b.winfo_rooty()+b.winfo_height(),window.winfo_rooty()+window.winfo_height())
    def test_editor_copy_control_actually_visible(self):
        r=self.ready();self.app.edit_reply(r);self.pump()
        self.assert_control_visible(self.app.editor,'复制修改稿')
        self.assert_control_visible(self.app.editor,'我已核对事实、时间和承诺')
    def test_paste_analyze_control_actually_visible(self):
        self.app.text_dialog();self.pump()
        self.assert_control_visible(self.app.text_editor,'生成回复')
    def test_settings_save_control_actually_visible(self):
        self.app.settings_dialog();self.pump()
        self.assert_control_visible(self.app.settings_window,'保存外观')
    def test_context_close_control_actually_visible(self):
        self.ready();self.app.context_dialog();self.pump()
        self.assert_control_visible(self.root,'收起对话')

    def test_worker_does_not_retain_tk_app(self):
        import threading
        release=threading.Event();started=threading.Event()
        class WaitingEngine:
            def run(inner,**kwargs):
                started.set();release.wait(2)
                from jev_feishu.overlay.engine import Outcome
                return Outcome('s','手动文本',notice='已停止')
        self.app.cfg=replace(self.cfg,allow_cloud=True)
        self.app.engine=WaitingEngine();self.app.text_input='我：待确认。\n同事：什么时候能好？'
        with patch.dict(os.environ,{'JEV_API_KEY':'test','LLM_API_KEY':'test'}):self.app.analyze()
        self.assertTrue(started.wait(1))
        target=self.app.worker._target
        self.assertNotIn('self',target.__code__.co_freevars)
        worker=self.app.worker;self.app.close();release.set();worker.join(2)
        self.assertFalse(worker.is_alive());self.assertIsNone(self.app.auto)

if __name__=='__main__':unittest.main()
