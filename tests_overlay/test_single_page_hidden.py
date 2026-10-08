"""Real Tk widgets kept withdrawn; never visual QA or system clipboard proof."""
import tempfile
import tkinter as tk
from tkinter import ttk
from pathlib import Path
import unittest
from jev_feishu.config import Config, Source
from jev_feishu.overlay.settings import OverlaySettings
from jev_feishu.overlay.ui import OverlayApp
from jev_feishu.overlay.viewmodel import replies


def widgets(w):
    yield w
    for child in w.winfo_children():yield from widgets(child)


class HiddenSinglePageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=tk.Tk();self.root.withdraw()
        self.clipboard='untouched'
        self.root.clipboard_clear=lambda:setattr(self,'clipboard','')
        self.root.clipboard_append=lambda s:setattr(self,'clipboard',self.clipboard+s)
        self.root.clipboard_get=lambda:self.clipboard
        cfg=Config(base=Path(self.tmp.name),sources=(Source('测试',chat_id='oc_synthetic',enabled=True),))
        self.app=OverlayApp(self.root,cfg,OverlaySettings())
        self.errors=[];self.root.report_callback_exception=lambda *e:self.errors.append(e)
        self.app.load_demo();self.root.update()

    def tearDown(self):
        self.assertEqual(self.root.state(),'withdrawn')
        self.app.close();self.tmp.cleanup();self.assertFalse(self.errors)

    def texts(self):
        return '\n'.join(str(w['text']) for w in widgets(self.root) if 'text' in w.keys())

    def first(self):return replies(self.app.result,.7)[0]

    def test_single_page_contains_source_all_decisions_and_reply(self):
        self.assertFalse(self.app.tab_buttons)
        self.assertIn('明天下午能给我一个可演示版本吗',self.texts())
        for text in ['可能诉求','沟通状况','下一步建议','信息够不够回复','候选回复排序','草稿 1','草稿 2','草稿 3','复制回复']:
            self.assertIn(text,self.texts())
        self.assertEqual(self.app.reply_area.pack_info()['side'],'bottom')

    def test_context_and_runtime_expand_without_new_window(self):
        self.app.context_dialog();self.app.toggle_runtime();self.app.toggle_raw()
        self.assertFalse(any(isinstance(w,tk.Toplevel) for w in widgets(self.root)))
        self.assertIn('本次主要回复',self.texts())
        self.assertIn('业务动作执行',self.texts())
        self.assertIn('question',self.texts())

    def test_inline_edit_confirms_and_copies_without_replacing_scored_candidate(self):
        r=self.first();before=list(self.app.result.candidates)
        self.app.edit_reply(r);self.root.update()
        self.assertIsInstance(self.app.editor,tk.Frame)
        self.assertFalse(any(isinstance(w,tk.Toplevel) for w in widgets(self.root)))
        box=self.app.editor_text;box.delete('1.0','end');box.insert('1.0','确认演示范围后再回复你。')
        self.root.update();self.app.editor_confirm.set(True);self.app.editor_copy();self.root.update()
        self.assertEqual(self.clipboard,'确认演示范围后再回复你。')
        self.assertEqual(self.app.result.candidates,before);self.assertFalse(self.app.filling)
        self.assertIsNone(self.app.editor)

    def test_source_switch_cancels_inline_editor_and_blocks_old_callback(self):
        self.app.edit_reply(self.first());callback=self.app.editor_copy
        self.app.editor_confirm.set(True);self.app.alias.set('测试');self.app._source_changed()
        callback()
        self.assertEqual(self.clipboard,'untouched');self.assertIsNone(self.app.editor)

    def test_new_round_blocks_old_editor_even_with_identical_reply(self):
        self.app.edit_reply(self.first());callback=self.app.editor_copy
        self.app.editor_confirm.set(True);self.app.load_demo();callback()
        self.assertEqual(self.clipboard,'untouched')

    def test_copied_edit_remains_visible_and_cannot_use_original_fill_score(self):
        r=self.first();self.app.edit_reply(r)
        box=self.app.editor_text;box.delete('1.0','end');box.insert('1.0','这是我核对后的修改稿。')
        self.root.update();self.app.editor_confirm.set(True);self.app.editor_copy()
        self.assertIn('这是我核对后的修改稿。',self.texts())
        self.assertIn('修改稿 · 未重新核查',self.texts())
        self.assertTrue(self.app.fill_buttons[r.key].instate(['disabled']))
        self.clipboard='';self.app.copy_buttons[r.key].invoke()
        self.assertEqual(self.clipboard,'这是我核对后的修改稿。')
        self.app.load_demo();self.assertNotIn('这是我核对后的修改稿。',self.texts())

    def test_same_result_disclosure_preserves_unsaved_edit(self):
        self.app.edit_reply(self.first());box=self.app.editor_text
        box.delete('1.0','end');box.insert('1.0','保留这个未复制的修改。')
        self.app.toggle_runtime();self.root.update()
        self.assertEqual(box.get('1.0','end-1c'),'保留这个未复制的修改。')

    def test_editor_buttons_are_created_in_visual_column_order(self):
        self.app.edit_reply(self.first())
        buttons=[w for w in widgets(self.app.editor) if isinstance(w,ttk.Button)]
        self.assertEqual([w['text'] for w in buttons],['恢复原文','取消','复制修改稿'])
        self.assertEqual([int(w.grid_info()['column']) for w in buttons],[0,2,3])

    def test_alternatives_do_not_duplicate_pinned_recommendation(self):
        self.app.toggle_alternatives();self.root.update()
        self.assertEqual(len(self.app.copy_buttons),3)

    def test_pause_disables_adoption_of_inflight_old_result(self):
        r=self.first();self.app.busy=True;self.app.pause();self.app.busy=False
        self.app.copy_reply(r);self.assertEqual(self.clipboard,'untouched')

    def test_window_follow_does_not_change_source_or_analysis_epoch(self):
        original_alias=self.app.alias.get();original_epoch=self.app.epoch
        class FakeFollower:
            def poll(self):return {'x':80,'y':80,'width':1000,'height':800}
            def close(self):pass
        self.app.follower=FakeFollower();self.app.follow_enabled.set(True)
        self.app._follow_tick()
        self.assertEqual(self.app.alias.get(),original_alias)
        self.assertEqual(self.app.epoch,original_epoch)
