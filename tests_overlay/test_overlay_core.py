from __future__ import annotations
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest

from jev_feishu.config import Config, Source
from jev_feishu.demo import DemoClient, TRANSCRIPT
from jev_feishu.errors import ConfigError, CopilotError, PermissionDenied
from jev_feishu.messages import Message
from jev_feishu.models import Analyzer
from jev_feishu.overlay.engine import OverlayEngine, demo_result
from jev_feishu.overlay.input_bridge import InputBridge
from jev_feishu.overlay.settings import OverlaySettings, load_settings
from jev_feishu.overlay.viewmodel import all_decisions, replies, Progress

class ViewTests(unittest.TestCase):
    def setUp(self):self.r=demo_result(Path('/tmp'))
    def test_all_eight_decisions_exposed(self):self.assertEqual(len(all_decisions(self.r)),8)
    def test_all_three_types(self):self.assertEqual({r.kind for r in all_decisions(self.r)},{'Choice','Score','Noul'})
    def test_noul_does_not_invent_confidence(self):
        self.r.judgement['facts_sufficient']['confidence']=.99
        d=next(d for d in all_decisions(self.r) if d.key=='facts_sufficient')
        self.assertIsNone(d.confidence);self.assertEqual(d.probability,.2)
        self.assertEqual(d.distribution,(('true','是',.2),('false','否',.8)))
    def test_score_not_normalized_to_nine(self):
        d=next(d for d in all_decisions(self.r) if d.kind=='Score')
        self.assertEqual(d.value,'0.10 / 4');self.assertIsNone(d.probability)
    def test_choice_probability_and_confidence_distinct(self):
        d=all_decisions(self.r)[0];self.assertEqual(d.confidence,.75);self.assertEqual(d.probability,.8)
    def test_action_has_not_been_executed(self):
        d=next(d for d in all_decisions(self.r) if d.key=='next_action');self.assertIn('未执行',d.note)
    def test_ranking_includes_none(self):
        d=next(d for d in all_decisions(self.r) if d.key=='best_reply')
        self.assertIn('none',[x[0] for x in d.distribution]);self.assertAlmostEqual(sum(v for _,_,v in d.distribution),1)
    def test_reply_order_does_not_renormalize(self):
        rr=replies(self.r,.7);self.assertEqual(rr[0].key,'reply_2');self.assertEqual(rr[0].probability,.6)
        self.assertAlmostEqual(sum(r.probability for r in rr),.94)
    def test_low_grounding_not_usable(self):
        self.r.ranking['grounded_2']['noul']=.2
        r=replies(self.r,.7)[0];self.assertFalse(r.usable);self.assertFalse(r.recommended)
    def test_none_blocks_fill(self):
        self.r.ranking['best_reply']['choice']='none'
        self.assertTrue(all(not r.usable for r in replies(self.r,.7)))
    def test_pending_no_default_probability(self):
        self.r.ranking={}
        self.assertTrue(all(r.probability is None and not r.usable for r in replies(self.r,.7)))
    def test_failed_steps_not_success(self):
        p=Progress();p.update('capture','done');p.update('judgement','failed');p.update('candidates','done');p.update('ranking','skipped')
        self.assertEqual(p.succeeded,2);self.assertEqual(p.terminal,4);self.assertEqual(p.summary,'已成功 2/4 步')
    def test_progress_invalid_stage(self):
        with self.assertRaises(ValueError):Progress().update('all','done')

class SettingTests(unittest.TestCase):
    def test_defaults_deny_fill(self):self.assertFalse(OverlaySettings().allow_fill)
    def test_fill_requires_bundle(self):
        with self.assertRaises(ConfigError):OverlaySettings(allow_fill=True).validate()
    def test_bundle_wildcards_rejected(self):
        with self.assertRaises(ConfigError):OverlaySettings(allowed_bundle_ids=['com.*']).validate()
    def test_nonbool_consent_rejected(self):
        with self.assertRaises(ConfigError):OverlaySettings(allow_fill='true').validate()
    def test_bad_opacity(self):
        with self.assertRaises(ConfigError):OverlaySettings(opacity=0.1).validate()
    def test_no_rapid_polling(self):
        with self.assertRaises(ConfigError):OverlaySettings(refresh_seconds=1).validate()
    def test_template_load(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'overlay.toml';p.write_text((Path(__file__).parents[1]/'overlay.example.toml').read_text());p.chmod(0o600)
            self.assertEqual(load_settings(p).width,490)
    def test_missing_file_returns_defaults(self):self.assertEqual(load_settings(Path('/nonexistent/overlay.toml')),OverlaySettings())

class FakeLark:
    def __init__(self,owner):
        self.calls=0;self.messages=[Message('m1',owner,'user','时间还未确认',1),Message('m2','ou_other','user','明天可演示吗',2)]
    def read_source(self,alias):self.calls+=1;return self.messages,False
    def send_owner(self,*args):raise AssertionError('overlay must never send')

class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
        self.cfg=Config(base=self.base,owner_open_id='ou_owner_test',allow_cloud=True,allow_bot_send=False,sources=(Source('同事',chat_id='oc_test123',enabled=True),))
        self.lark=FakeLark(self.cfg.owner_open_id)
        self.analyzer=Analyzer(self.cfg,DemoClient(),demo=True)
        self.engine=OverlayEngine(self.cfg,lark=self.lark,analyzer=self.analyzer)
        self.env=patch.dict(os.environ,{'JEV_API_KEY':'synthetic','LLM_API_KEY':'synthetic'});self.env.start()
    def tearDown(self):self.env.stop();self.temp.cleanup()
    def test_overlay_needs_no_bot_send(self):
        out=self.engine.run(alias='同事');self.assertTrue(out.result.judgement);self.assertEqual(self.lark.calls,1)
    def test_unlisted_denied_before_read(self):
        with self.assertRaises(PermissionDenied):self.engine.run(alias='任意群')
        self.assertEqual(self.lark.calls,0)
    def test_cloud_denied_before_read(self):
        engine=OverlayEngine(replace(self.cfg,allow_cloud=False),lark=self.lark)
        with self.assertRaises(PermissionDenied):engine.run(alias='同事')
        self.assertEqual(self.lark.calls,0)
    def test_same_snapshot_does_not_analyze(self):
        out=self.engine.run(alias='同事')
        with patch.object(self.analyzer,'analyze',side_effect=AssertionError('must not run')):
            again=self.engine.run(alias='同事',automatic=True,previous=out.signature)
        self.assertIsNone(again.result);self.assertFalse(again.invalidates)
    def test_own_reply_invalidates_old_result(self):
        out=self.engine.run(alias='同事')
        self.lark.messages.append(Message('m3',self.cfg.owner_open_id,'user','我已回复',3))
        new=self.engine.run(alias='同事',automatic=True,previous=out.signature)
        self.assertTrue(new.invalidates);self.assertIsNone(new.result)
    def test_bot_latest_no_auto_analysis(self):
        self.lark.messages.append(Message('m3','bot','app','通知',3))
        self.assertTrue(self.engine.run(alias='同事',automatic=True).invalidates)
    def test_edits_change_signature(self):
        first=self.engine.run(alias='同事')
        self.lark.messages[-1]=replace(self.lark.messages[-1],text='后天可以吗')
        new=self.engine.run(alias='同事',automatic=True,previous=first.signature)
        self.assertNotEqual(first.signature,new.signature);self.assertIsNotNone(new.result)
    def test_manual_text_does_not_read_cli(self):
        result=self.engine.run(text=TRANSCRIPT)
        self.assertEqual(self.lark.calls,0);self.assertEqual(result.alias,'手动文本')
    def test_auto_text_rejected(self):
        with self.assertRaises(CopilotError):self.engine.run(text=TRANSCRIPT,automatic=True)
    def test_cancel_before_read(self):
        stop=threading.Event();stop.set()
        with self.assertRaises(CopilotError):self.engine.run(alias='同事',cancelled=stop)
        self.assertEqual(self.lark.calls,0)
    def test_stage_events_and_snapshots(self):
        events=[];self.engine.run(alias='同事',on_event=lambda *v:events.append(v))
        self.assertIn(('capture','done'),[(s,t) for s,t,_ in events])
        partial=next(r for s,t,r in events if s=='judgement' and t=='done')
        self.assertEqual(partial.ranking,{})
    def test_cancel_skips_ranking(self):
        stop=threading.Event()
        def emit(s,t,r):
            if s=='judgement' and t=='done':stop.set()
        with patch.object(self.analyzer.client,'rank',side_effect=AssertionError('ranking after cancel')):
            out=self.engine.run(alias='同事',cancelled=stop,on_event=emit)
        self.assertFalse(out.result.ranking)
    def test_state_contains_no_raw_conversation_at_rest(self):
        self.engine.run(alias='同事')
        for file in (self.base/'.state').iterdir():self.assertNotIn('明天可演示吗'.encode(),file.read_bytes())

class BridgeTests(unittest.TestCase):
    def bridge(self,runner):
        return InputBridge(Path('/tmp'),OverlaySettings(allow_fill=True,allowed_bundle_ids=('com.test.feishu',)),runner=runner)
    def test_disabled_never_spawns(self):
        bridge=InputBridge(Path('/tmp'),OverlaySettings(),runner=lambda *a,**kw: self.fail('spawned'))
        self.assertEqual(bridge.fill('内容',confirmed=True).status,'blocked')
    def test_unconfirmed_never_spawns(self):
        bridge=self.bridge(lambda *a,**kw:self.fail('spawned'))
        self.assertEqual(bridge.fill('内容',confirmed=False).status,'blocked')
    def test_verified_requires_all_fields(self):
        bridge=self.bridge(lambda *a,**kw:SimpleNamespace(returncode=0,stdout=json.dumps({'status':'filled','verified':True,'sent':False})))
        with patch.object(bridge,'available',return_value=True):self.assertTrue(bridge.fill('内容',confirmed=True).verified)
    def test_without_readback_not_filled(self):
        bridge=self.bridge(lambda *a,**kw:SimpleNamespace(returncode=0,stdout=json.dumps({'status':'filled','verified':False,'sent':False})))
        with patch.object(bridge,'available',return_value=True):self.assertFalse(bridge.fill('内容',confirmed=True).verified)
    def test_sent_field_not_false_rejected(self):
        bridge=self.bridge(lambda *a,**kw:SimpleNamespace(returncode=0,stdout=json.dumps({'status':'filled','verified':True,'sent':True})))
        with patch.object(bridge,'available',return_value=True):self.assertFalse(bridge.fill('内容',confirmed=True).verified)
    def test_text_not_in_argv_or_secrets(self):
        calls=[]
        def runner(args,**kw):
            calls.append((args,kw));return SimpleNamespace(returncode=0,stdout='{"status":"filled","verified":true,"sent":false}')
        b=self.bridge(runner)
        with patch.object(b,'available',return_value=True),patch.dict(os.environ,{'JEV_API_KEY':'super_secret'}):b.fill('私人草稿',confirmed=True)
        args,kw=calls[0];self.assertNotIn('私人草稿',' '.join(args));self.assertNotIn('JEV_API_KEY',kw['env']);self.assertFalse(kw['shell'])
    def test_timeout_no_retry(self):
        calls=[]
        def runner(*args,**kw):calls.append(1);raise subprocess.TimeoutExpired('helper',12)
        b=self.bridge(runner)
        with patch.object(b,'available',return_value=True):out=b.fill('内容',confirmed=True)
        self.assertEqual(out.status,'unknown');self.assertEqual(len(calls),1)
    def test_existing_draft_blocks(self):
        b=self.bridge(lambda *a,**kw:SimpleNamespace(returncode=2,stdout='{"status":"blocked","reason":"nonempty_or_unreadable"}'))
        with patch.object(b,'available',return_value=True):out=b.fill('内容',confirmed=True)
        self.assertEqual(out.status,'blocked');self.assertIn('不会覆盖',out.message)
    def test_newline_no_shell(self):
        calls=[]
        def runner(args,**kw):calls.append(json.loads(kw['input']));return SimpleNamespace(returncode=2,stdout='{}')
        b=self.bridge(runner)
        with patch.object(b,'available',return_value=True):b.fill('一\n二',confirmed=True)
        self.assertEqual(calls[0]['text'],'一\n二')
    def test_helper_has_no_keypress_fallback(self):
        source=(Path(__file__).parents[1]/'native/InputHelper.swift').read_text()
        self.assertNotIn('CGEvent(',source);self.assertNotIn('keyCode',source);self.assertNotIn('NSPasteboard',source)
        self.assertIn('CFEqual(current, target)',source)

if __name__=='__main__':unittest.main()
