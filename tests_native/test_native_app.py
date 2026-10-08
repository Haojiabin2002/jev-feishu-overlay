"""Offscreen native-control checks; no foreground window or system clipboard writes."""
from pathlib import Path
from types import SimpleNamespace
from dataclasses import replace
from unittest.mock import Mock, patch
import tempfile
import unittest
import sys
import AppKit
from jev_feishu.config import Config, Source
from jev_feishu.overlay.settings import OverlaySettings
from jev_feishu.overlay.native_app import FeishuHudController
from jev_feishu.overlay.engine import Outcome, demo_result
from jev_feishu.models import AnalysisResult


class PrivatePasteboard:
    def __init__(self):self.value='untouched'
    def clearContents(self):self.value=''
    def setString_forType_(self,value,_):self.value=value;return True
    def stringForType_(self,_):return self.value


class NativeAppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=AppKit.NSApplication.sharedApplication()
        cls.app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        cfg=Config(base=Path(self.tmp.name),sources=(Source('群A',chat_id='oc_test_a',enabled=True),Source('群B',chat_id='oc_test_b',enabled=True)))
        self.c=FeishuHudController.alloc().init().configure(cfg,OverlaySettings(),demo=True,headless=True)
        self.pb=PrivatePasteboard();self.c.test_pasteboard=self.pb

    def tearDown(self):
        self.assertFalse(self.c.panel.isVisible())
        self.c.closed=True;self.c.cancel.set();self.c.panel.close()

    def test_upstream_controls_build_without_ocr_or_local_models(self):
        self.assertIsInstance(self.c.panel,AppKit.NSPanel)
        self.assertEqual(len(self.c._rows[0]),3)
        self.assertNotIn('torch',sys.modules)
        self.assertNotIn('perception',sys.modules)
        self.assertTrue(self.c._rows[0][0]['btn'].isEnabled())

    def test_eight_decisions_and_execution_states_are_available(self):
        text=self.c._details_text()
        self.assertEqual(text.count('（Choice）'),3)
        self.assertEqual(text.count('（Score）'),1)
        self.assertEqual(text.count('（Noul）'),4)
        self.assertIn('/ 4',text);self.assertIn('实际执行状态',text)

    def test_actual_native_button_action_copies_to_private_test_pasteboard(self):
        self.c._rows[0][0]['btn'].performClick_(None)
        self.assertEqual(self.pb.value,self.c.shown_replies[0].text)

    def test_clipboard_failure_is_unconfirmed_without_retry(self):
        class FailedPasteboard(PrivatePasteboard):
            calls=0
            def setString_forType_(self,*_):self.calls+=1;raise RuntimeError('unavailable')
        failed=FailedPasteboard();self.c.test_pasteboard=failed
        self.c.copyCandidate_(SimpleNamespace(tag=lambda:0))
        self.assertEqual(failed.calls,1)
        self.assertEqual(str(self.c.rows['status'].stringValue()),'复制未确认')

    def test_edited_reply_requires_confirmation_and_preserves_original_scores(self):
        original=list(self.c.result.candidates)
        self.c._rows[0][0]['fill_btn'].performClick_(None)
        field=self.c._rows[0][0]['text'];field.setStringValue_('我确认后再同步给你。')
        self.c.controlTextDidChange_(SimpleNamespace(object=lambda:field))
        self.c._rows[0][0]['btn'].performClick_(None)
        self.assertEqual(self.pb.value,'untouched')
        self.c.edit_checks[0].performClick_(None)
        self.c._rows[0][0]['btn'].performClick_(None)
        self.assertEqual(self.pb.value,'我确认后再同步给你。')
        self.assertEqual(self.c.result.candidates,original)
        self.assertIn('模型未重新评估',str(self.c.rows['status'].stringValue()))
        self.assertTrue(self.c._rows[0][0]['prob'].isHidden())

    def test_more_editing_revokes_previous_confirmation(self):
        self.c.editCandidate_(SimpleNamespace(tag=lambda:0))
        self.c.edit_checks[0].performClick_(None)
        field=self.c._rows[0][0]['text'];field.setStringValue_('新增承诺')
        self.c.controlTextDidChange_(SimpleNamespace(object=lambda:field))
        self.assertFalse(self.c.edit_checks[0].state())

    def test_edited_score_stays_hidden_after_collapse_and_restore(self):
        self.c.editCandidate_(SimpleNamespace(tag=lambda:0))
        self.c.collapsePanel_(None);self.c.collapsePanel_(None)
        self.assertTrue(self.c._rows[0][0]['prob'].isHidden())
        self.assertFalse(self.c.edit_checks[0].isHidden())

    def test_expanded_details_update_after_copy(self):
        self.c.toggleDetails_(None)
        self.c._rows[0][0]['btn'].performClick_(None)
        self.assertIn('已复制，未发送',str(self.c._details_control.stringValue()))

    def test_expanded_details_drop_old_source_and_decisions(self):
        self.c.toggleDetails_(None)
        self.c.toneChanged_(SimpleNamespace(titleOfSelectedItem=lambda:'群B'))
        text=str(self.c._details_control.stringValue())
        self.assertIn('当前来源：群B',text)
        self.assertNotIn('当前来源：合成示例',text)
        self.assertNotIn('8项模型判断',text)

    def test_expanded_details_update_when_paused(self):
        self.c.progress.update('capture','running')
        self.c.toggleDetails_(None)
        self.c.togglePause_(None)
        self.assertNotIn('读取会话：执行中',str(self.c._details_control.stringValue()))
        self.assertIn('读取会话：已停止',str(self.c._details_control.stringValue()))

    def test_annotation_status_refresh_does_not_resize_main_window(self):
        self.c.demo=False
        self.c.next_read=float('inf')
        annotation=SimpleNamespace(last_reason='matched')
        annotation.tick=lambda:setattr(annotation,'last_reason','editing')
        self.c.message_annotations=annotation
        self.c.toggleDetails_(None)
        frame=self.c.panel.frame()
        with patch.object(self.c,'_relayout',wraps=self.c._relayout) as relayout:
            self.c.tick_(None)
            relayout.assert_not_called()
        self.assertEqual(self.c.panel.frame(),frame)
        self.assertIn('输入期间',str(self.c._details_control.stringValue()))

    def test_unchanged_long_details_do_not_bounce_window_height(self):
        self.c.toggleDetails_(None)
        panel=self.c.panel
        calls=[]
        class PanelSpy:
            def __getattr__(self,name):return getattr(panel,name)
            def setFrame_display_(self,frame,display):
                calls.append(frame);panel.setFrame_display_(frame,display)
        self.c.panel=PanelSpy()
        try:self.c._refresh_details()
        finally:self.c.panel=panel
        self.assertEqual(calls,[])

    def test_manual_read_rechecks_annotation_focus_but_timer_does_not(self):
        annotation=SimpleNamespace(request_refresh=Mock())
        self.c.message_annotations=annotation
        self.c.read_button.performClick_(None)
        annotation.request_refresh.assert_called_once()
        self.c.refreshMessages_(None)
        annotation.request_refresh.assert_called_once()

    def test_source_switch_clears_old_message_and_adoption(self):
        self.c._render('status','已读取旧来源消息')
        self.c.toneChanged_(SimpleNamespace(titleOfSelectedItem=lambda:'群B'))
        self.assertIsNone(self.c.result)
        self.assertEqual(str(self.c.rows['message'].stringValue()),'')
        self.assertNotIn('旧来源',str(self.c.rows['status'].stringValue()))
        self.c.copyCandidate_(SimpleNamespace(tag=lambda:0));self.assertEqual(self.pb.value,'untouched')

    def test_pause_and_resume_do_not_reenable_old_reply(self):
        self.c.togglePause_(None);self.c.togglePause_(None)
        self.c.copyCandidate_(SimpleNamespace(tag=lambda:0))
        self.assertEqual(self.pb.value,'untouched')

    def test_visible_pause_control_blocks_read_and_requires_a_fresh_demo_read(self):
        self.c.pause_button.performClick_(None)
        self.assertTrue(self.c._paused)
        self.assertFalse(self.c.read_button.isEnabled())
        self.assertFalse(self.c.analyze_button.isEnabled())
        self.c.pause_button.performClick_(None)
        self.assertTrue(self.c.read_button.isEnabled())
        self.c.copyCandidate_(SimpleNamespace(tag=lambda:0))
        self.assertEqual(self.pb.value,'untouched')
        self.c.read_button.performClick_(None)
        self.c._rows[0][0]['btn'].performClick_(None)
        self.assertEqual(self.pb.value,self.c.shown_replies[0].text)

    def test_late_old_analysis_cannot_replace_new_source(self):
        old=self.c.epoch
        self.c.toneChanged_(SimpleNamespace(titleOfSelectedItem=lambda:'群B'))
        self.c.events.put((old,'analysis',Outcome('old','群A',demo_result(self.c.cfg.base))))
        self.c.tick_(None);self.assertIsNone(self.c.result)

    def test_old_finished_cannot_clear_a_new_worker(self):
        self.c.analysis_busy=True;self.c.analysis_job_epoch=9;self.c.epoch=9
        self.c.events.put((3,'analysis_done',None));self.c.tick_(None)
        self.assertTrue(self.c.analysis_busy)

    def test_upstream_collapse_and_restore_preserve_document(self):
        original=self.c.panel.frame().size.height
        self.c.collapsePanel_(None);self.assertTrue(self.c._collapsed)
        self.assertLess(self.c.panel.frame().size.height,original)
        self.c.collapsePanel_(None);self.assertFalse(self.c._collapsed)
        self.assertGreaterEqual(self.c.panel.frame().size.height,original)

    def test_details_expand_inside_same_panel(self):
        self.c.toggleDetails_(None)
        self.assertTrue(self.c.expanded_details)
        self.assertIn('8项模型判断',str(self.c._details_control.stringValue()))
        self.assertFalse(self.c.panel.isVisible())

    def test_read_only_preview_reports_zero_model_decisions(self):
        self.c.result=AnalysisResult(self.c.result.state)
        self.c._render_result()
        text=self.c._details_text()
        self.assertIn('模型判断：已取得 0 / 8 项',text)
        self.assertNotIn('8项模型判断',text)

    def test_original_conversation_has_direct_inline_access(self):
        self.c.context_button.performClick_(None)
        self.assertTrue(self.c.expanded_context)
        text=str(self.c._details_control.stringValue())
        self.assertIn('本次主要回复',text)
        self.assertNotIn('8项模型判断',text)
        self.c.details_button.performClick_(None)
        self.assertFalse(self.c.expanded_context)
        self.assertIn('8项模型判断',str(self.c._details_control.stringValue()))

    def test_detail_reply_numbers_match_display_sort_order(self):
        self.assertEqual(self.c.shown_replies[0].key,'reply_2')
        text=self.c._details_text()
        self.assertIn('选择判断（Choice）：回复 1',text)
        self.assertIn('回复 1 60%',text)
        self.assertNotIn('reply_',text)

    def test_history_is_read_only_and_preserves_original_scores(self):
        self.c._select_history_record('demo-history')
        self.assertIsNotNone(self.c.history_record)
        self.assertIn('历史',str(self.c.rows['status'].stringValue()))
        self.assertFalse(self.c._rows[0][0]['btn'].isEnabled())
        self.assertFalse(self.c._rows[0][0]['fill_btn'].isEnabled())
        self.c.copyCandidate_(SimpleNamespace(tag=lambda:0))
        self.c.editCandidate_(SimpleNamespace(tag=lambda:0))
        self.assertEqual(self.pb.value,'untouched');self.assertFalse(self.c.edited)
        self.assertEqual(self.c.history_record.result,self.c.demo_history['demo-history'].result)

    def test_live_capture_does_not_replace_the_history_being_read(self):
        self.c._select_history_record('demo-history')
        old=str(self.c.rows['message'].stringValue())
        fresh=demo_result(self.c.cfg.base)
        fresh.state['chat']['messages'][-1]['text']='新的会话消息，不应覆盖正在查看的记录。'
        self.c.events.put((self.c.epoch,'capture',Outcome('changed','合成示例',fresh)))
        self.c.tick_(None)
        self.assertEqual(str(self.c.rows['message'].stringValue()),old)
        self.assertIn('历史',str(self.c.rows['status'].stringValue()))
        self.c._select_history_record('current')
        self.assertIn('新的会话消息',str(self.c.rows['message'].stringValue()))
        self.assertFalse(self.c._rows[0][0]['btn'].isEnabled())

    def test_switching_source_exits_history_and_keeps_adoption_blocked(self):
        self.c._select_history_record('demo-history')
        self.c.toneChanged_(SimpleNamespace(titleOfSelectedItem=lambda:'群B'))
        self.assertIsNone(self.c.history_record)
        self.assertEqual(str(self.c.rows['message'].stringValue()),'')
        self.c.copyCandidate_(SimpleNamespace(tag=lambda:0));self.assertEqual(self.pb.value,'untouched')

    def test_history_remains_visible_when_paused(self):
        self.c._select_history_record('demo-history')
        self.c.togglePause_(None)
        self.assertFalse(self.c._rows[0][0]['text'].isHidden())
        self.assertFalse(self.c._rows[0][0]['btn'].isEnabled())

    def test_return_to_current_after_error_clears_history_contents(self):
        self.c._select_history_record('demo-history')
        self.c.result=None;self.c.adoption_epoch=-1
        self.c._select_history_record('current')
        self.assertEqual(str(self.c.rows['message'].stringValue()),'')
        self.assertFalse(self.c.shown_replies)

    def test_history_return_button_only_changes_view_without_running_model(self):
        self.c._select_history_record('demo-history')
        self.assertEqual(str(self.c.analyze_button.title()),'回当前')
        self.c.analyze_button.performClick_(None)
        self.assertIsNone(self.c.history_record)
        self.assertFalse(self.c.analysis_busy)
        self.assertEqual(str(self.c.analyze_button.title()),'分析')

    def test_read_recovers_same_messages_after_a_temporary_error(self):
        preview=AnalysisResult(self.c.result.state)
        self.c.capture_signature='unchanged'
        self.c.events.put((self.c.epoch,'error','临时读取失败'))
        self.c.tick_(None)
        self.assertIsNone(self.c.result)
        self.c.events.put((self.c.epoch,'capture',Outcome('unchanged','合成示例',preview,preview_only=True)))
        self.c.tick_(None)
        self.assertIsNotNone(self.c.result)
        self.assertIn('明天下午',str(self.c.rows['message'].stringValue()))
        self.assertEqual(self.c.adoption_epoch,-1)

    def test_unchanged_read_explains_that_analysis_is_manual(self):
        preview=AnalysisResult(self.c.result.state)
        self.c.result=preview;self.c.capture_signature='same'
        self.c.events.put((self.c.epoch,'capture',Outcome('same','合成示例',preview,preview_only=True)))
        self.c.tick_(None)
        status=str(self.c.rows['status'].stringValue())
        self.assertIn('已读 4 条',status)
        self.assertIn('分析',status)

    def test_collapsed_panel_has_an_explicit_expand_control(self):
        self.c.collapsePanel_(None)
        self.assertFalse(self.c.expand_button.isHidden())
        self.c.expand_button.performClick_(None)
        self.assertFalse(self.c._collapsed)
        self.assertTrue(self.c.expand_button.isHidden())
        self.assertFalse(self.c.panel.isVisible())

    def test_auto_analysis_cannot_be_enabled_while_paused(self):
        self.c.demo=False;self.c.alias='群A';self.c.togglePause_(None)
        self.c.toggleAutoAnalyze_(None)
        self.assertFalse(self.c.auto_analyze)
        self.assertFalse(self.c.pending_auto)

    def test_inline_auto_control_reflects_pause_and_source_change(self):
        self.c._set_auto(True)
        self.assertTrue(self.c.auto_check.state())
        self.c.togglePause_(None)
        self.assertFalse(self.c.auto_check.state())

    def test_new_result_does_not_reopen_collapsed_panel(self):
        self.c.collapsePanel_(None);height=self.c.panel.frame().size.height
        self.c._render_result('后台结果更新')
        self.assertTrue(self.c._collapsed)
        self.assertEqual(self.c.panel.frame().size.height,height)

    def test_no_winner_blocks_original_adoption(self):
        self.c.result.ranking['best_reply']['choice']='none';self.c._render_result()
        self.assertFalse(self.c._rows[0][0]['btn'].isEnabled())
        self.c.copyCandidate_(SimpleNamespace(tag=lambda:0));self.assertEqual(self.pb.value,'untouched')

    def test_failed_read_marks_actual_failure_instead_of_staying_running(self):
        self.c.progress.update('capture','running')
        self.c.events.put((self.c.epoch,'error','读取失败'))
        self.c.tick_(None)
        self.assertEqual(self.c.progress.states['capture'],'failed')

    def test_reused_docking_tracks_geometry_after_debounce_without_showing_window(self):
        self.c._position_near({'x':100,'y':100,'w':700,'h':700})
        before=self.c.panel.frame().origin
        self.c._position_near({'x':120,'y':110,'w':700,'h':700})
        self.c._position_near({'x':120,'y':110,'w':700,'h':700})
        after=self.c.panel.frame().origin
        self.assertAlmostEqual(after.x-before.x,20)
        self.assertAlmostEqual(after.y-before.y,-10)
        self.assertFalse(self.c.panel.isVisible())

    def test_automatic_unchanged_snapshot_does_not_start_or_invalidate(self):
        self.c.demo=False;self.c.alias='群A';self.c.analysis_signature='same';self.c.capture_signature='same'
        before=self.c.epoch;self.c._begin_analysis(True)
        self.assertFalse(self.c.analysis_busy);self.assertEqual(self.c.epoch,before)

    def test_persistent_source_consent_stays_scoped_after_switching(self):
        c=self.c;c.demo=False;c.alias='群A'
        c.cfg=replace(c.cfg,sources=(replace(c.cfg.sources[0],allow_cloud=True),c.cfg.sources[1]))
        with patch.dict('os.environ',{'JEV_API_KEY':'synthetic','LLM_API_KEY':'synthetic'}),patch('jev_feishu.overlay.native_app.threading.Thread') as thread:
            c._begin_analysis(False)
            self.assertTrue(c.analysis_busy)
            self.assertFalse(c.cfg.allow_cloud)
            self.assertFalse(c.approved_aliases)
            thread.return_value.start.assert_called_once()
            c.analysis_busy=False;c.toneChanged_(SimpleNamespace(titleOfSelectedItem=lambda:'群B'))
            c._begin_analysis(False)
            self.assertFalse(c.analysis_busy)
            thread.return_value.start.assert_called_once()

    def test_pause_and_source_switch_disable_automatic_analysis(self):
        self.c._set_auto(True);self.c.togglePause_(None);self.assertFalse(self.c.auto_analyze)
        self.c.togglePause_(None);self.c._set_auto(True)
        self.c.toneChanged_(SimpleNamespace(titleOfSelectedItem=lambda:'群B'))
        self.assertFalse(self.c.auto_analyze)
