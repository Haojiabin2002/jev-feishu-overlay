from pathlib import Path
from unittest.mock import Mock
from unittest.mock import patch
from dataclasses import replace
import tempfile
import threading
import unittest
from jev_feishu.config import Config, Source
from jev_feishu.messages import Message
from jev_feishu.overlay.engine import OverlayEngine,demo_result
from jev_feishu.overlay.history import HistoryStore
from jev_feishu.errors import CopilotError
from jev_feishu.models import AnalysisResult


class LiveCaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.cfg=Config(base=Path(self.tmp.name),owner_open_id='ou_owner',allow_cloud=False,
                        sources=(Source('群聊',chat_id='oc_testgroup',enabled=True),))
        self.lark=Mock();self.lark.read_source.return_value=([Message('m1','ou_other','user','明天演示范围是什么？',1000)],False)
        self.model=Mock();self.model.analyze.side_effect=AssertionError('capture must never call a model')
        self.engine=OverlayEngine(self.cfg,lark=self.lark,analyzer=self.model)

    def test_real_source_preview_does_not_require_model_key_or_cloud_consent(self):
        out=self.engine.capture(alias='群聊')
        self.assertTrue(out.preview_only);self.assertFalse(out.result.demo)
        self.assertEqual(len(out.result.state['chat']['messages']),1)
        self.assertFalse(out.result.judgement);self.assertFalse(out.result.candidates)
        self.model.analyze.assert_not_called()
        self.assertFalse(self.cfg.state_dir.exists())

    def test_unlisted_source_is_rejected_before_read(self):
        with self.assertRaises(CopilotError):self.engine.capture(alias='另一群')
        self.lark.read_source.assert_not_called()

    def test_pause_before_read_prevents_capture(self):
        stop=threading.Event();stop.set()
        with self.assertRaises(CopilotError):self.engine.capture(alias='群聊',cancelled=stop)
        self.lark.read_source.assert_not_called()

    def test_pause_after_read_discards_result(self):
        stop=threading.Event()
        def read(_):
            stop.set();return ([Message('m1','ou_other','user','消息',1000)],False)
        self.lark.read_source.side_effect=read
        with self.assertRaises(CopilotError):self.engine.capture(alias='群聊',cancelled=stop)

    def test_preview_and_analysis_share_the_same_snapshot_signature(self):
        model=Mock();model.analyze.side_effect=lambda state,**_:AnalysisResult(state)
        engine=OverlayEngine(replace(self.cfg,allow_cloud=True),lark=self.lark,analyzer=model)
        with patch.dict('os.environ',{'JEV_API_KEY':'synthetic','LLM_API_KEY':'synthetic'}):
            analyzed=engine.run(alias='群聊')
        self.assertEqual(engine.capture(alias='群聊').signature,analyzed.signature)

    def test_source_cloud_consent_is_honored_without_global_permission(self):
        cfg=replace(self.cfg,sources=(replace(self.cfg.sources[0],allow_cloud=True),))
        model=Mock();model.analyze.side_effect=lambda state,**_:AnalysisResult(state)
        with patch.dict('os.environ',{'JEV_API_KEY':'synthetic','LLM_API_KEY':'synthetic'}):
            out=OverlayEngine(cfg,lark=self.lark,analyzer=model).run(alias='群聊')
        self.assertIsNotNone(out.result)
        self.assertFalse(cfg.allow_cloud)
        self.lark.read_source.assert_called_once_with('群聊')

    def test_completed_engine_analysis_is_persisted_before_ui_can_replace_it(self):
        cfg=replace(self.cfg,allow_cloud=True)
        def result(state,**_):
            r=demo_result(cfg.base);r.state=state;r.demo=False;return r
        model=Mock();model.analyze.side_effect=result
        with patch.dict('os.environ',{'JEV_API_KEY':'synthetic','LLM_API_KEY':'synthetic'}):
            out=OverlayEngine(cfg,lark=self.lark,analyzer=model).run(alias='群聊')
        self.assertTrue(out.history_id);self.assertFalse(out.history_error)
        with HistoryStore(cfg) as history:
            saved=history.get('群聊',out.history_id)
            self.assertEqual(saved.result.state,out.result.state)
            self.assertEqual(saved.result.candidates,out.result.candidates)

    def test_repeated_message_text_retains_history_without_position_guessing(self):
        cfg=replace(self.cfg,allow_cloud=True)
        self.lark.read_source.return_value=([
            Message('first','ou_other','user','请核对这几条记录。',1000),
            Message('second','ou_other','user','请核对这几条记录。',2000)],False)
        model=Mock();model.analyze.side_effect=lambda state,**_:AnalysisResult(state)
        with patch.dict('os.environ',{'JEV_API_KEY':'synthetic','LLM_API_KEY':'synthetic'}):
            out=OverlayEngine(cfg,lark=self.lark,analyzer=model).run(alias='群聊')
        with HistoryStore(cfg) as history:
            saved=history.get('群聊',out.history_id)
            self.assertIsNotNone(saved)
            self.assertEqual(saved.anchor_hash,'')
