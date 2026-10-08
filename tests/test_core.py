from __future__ import annotations
import copy
from dataclasses import replace
from pathlib import Path
import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import urllib.error
import io

from jev_feishu.binding import save_binding, resolve_binding
from jev_feishu.config import Config, Source, load_config, load_env_file
from jev_feishu.errors import ConfigError, ContractError, CopilotError, PermissionDenied, ProviderError
from jev_feishu.lark import LarkCLI, LarkError, unwrap
from jev_feishu.messages import Message, build_state, parse_messages, pasted_messages, timestamp_ms
from jev_feishu.models import Analyzer, ModelClient, JsonTransport, parse_candidates, validate_answers
from jev_feishu.demo import DemoClient, TRANSCRIPT, REPLIES
from jev_feishu.privacy import clean_text, outbound_text
from jev_feishu.questions import judge_questions, ranking_questions
from jev_feishu.render import render
from jev_feishu.service import Service, parse_command
from jev_feishu.store import Store, instance_lock

OWNER = "ou_owner_test"
CHAT = "oc_private_bot"
SOURCE = Source("同事", chat_id="oc_project_test", enabled=True)

def cfg(base=Path("."), **kwargs):
    return Config(base=base, owner_open_id=OWNER, inbox_chat_id=CHAT, allow_cloud=True,
                  allow_bot_send=True, sources=(SOURCE,), **kwargs)

def raw_message(mid="om_abc", text="你好", sender=OWNER, kind="text", created="1790000000123", stype="user"):
    return {"message_id": mid, "msg_type": kind, "body": {"content": json.dumps({"text": text})},
            "sender": {"id": sender, "id_type": "open_id", "sender_type": stype},
            "create_time": created, "deleted": False}

class ConfigTests(unittest.TestCase):
    def test_display_name_does_not_change_authorized_alias(self):
        source=replace(SOURCE,display_name='Readable group name')
        c=replace(cfg(),sources=(source,));c.validate()
        self.assertEqual(c.source(source.alias).chat_id,source.chat_id)
        with self.assertRaises(PermissionDenied):c.source(source.display_name)

    def test_display_name_rejects_control_line_breaks(self):
        with self.assertRaises(ConfigError):replace(SOURCE,display_name='Group\nAnother').validate()

    def test_default_cloud_disabled(self):
        with self.assertRaises(PermissionDenied): Config(base=Path(".")).check_models()

    def test_string_consent_rejected(self):
        with self.assertRaises(ConfigError): replace(cfg(), allow_cloud="false").validate()

    def test_source_cloud_consent_does_not_authorize_other_sources(self):
        a=replace(SOURCE,allow_cloud=True)
        b=Source('另一群',chat_id='oc_other_group',enabled=True)
        original=replace(cfg(),allow_cloud=False,sources=(a,b))
        scoped=original.model_config_for(a.alias)
        with patch.dict(os.environ,{'JEV_API_KEY':'synthetic','LLM_API_KEY':'synthetic'}):
            scoped.check_models()
            with self.assertRaises(PermissionDenied):original.model_config_for(b.alias).check_models()
        with self.assertRaises(PermissionDenied):scoped.source(b.alias)
        self.assertFalse(original.allow_cloud)

    def test_source_string_consent_rejected(self):
        with self.assertRaises(ConfigError):
            replace(cfg(),sources=(replace(SOURCE,allow_cloud='false'),)).validate()

    def test_duplicate_sources_rejected(self):
        with self.assertRaises(ConfigError): replace(cfg(), sources=(SOURCE,SOURCE)).validate()

    def test_source_both_selectors_rejected(self):
        with self.assertRaises(ConfigError): Source("x", chat_id=CHAT, user_id=OWNER, enabled=True).validate()

    def test_unlisted_alias_denied(self):
        with self.assertRaises(PermissionDenied): cfg().source("oc_arbitrary")

    def test_http_endpoint_rejected(self):
        with self.assertRaises(ConfigError): replace(cfg(), jev_url="http://example.com").validate()

    def test_embedded_auth_endpoint_rejected(self):
        with self.assertRaises(ConfigError): replace(cfg(), jev_url="https://key@host/a").validate()

    def test_config_template_loads(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"config.toml"
            p.write_text((Path(__file__).parents[1]/"config.example.toml").read_text())
            p.chmod(0o600)
            result=load_config(p)
            self.assertFalse(result.allow_cloud)
            self.assertFalse(result.allow_bot_send)

    def test_dotenv_does_not_execute(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {}, clear=True):
            p=Path(d)/".env"; p.write_text('TEST_VALUE=$(touch /tmp/never_execute)\n'); p.chmod(0o600)
            load_env_file(p)
            self.assertEqual(os.environ["TEST_VALUE"], "$(touch /tmp/never_execute)")

    def test_env_explicit_wins(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {"TEST_VALUE":"original"}):
            p=Path(d)/".env";p.write_text("TEST_VALUE=replacement\n");p.chmod(0o600)
            load_env_file(p);self.assertEqual(os.environ["TEST_VALUE"],"original")

    def test_private_file_permissions(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/".env";p.write_text("X=y\n");p.chmod(0o644)
            with self.assertRaises(ConfigError):load_env_file(p)

class MessageTests(unittest.TestCase):
    def test_preserve_milliseconds(self):
        self.assertEqual(timestamp_ms("1790000000123"),1790000000123)

    def test_seconds_timestamp(self):
        self.assertEqual(timestamp_ms("1790000000"),1790000000000)

    def test_iso_timezone(self):
        self.assertEqual(timestamp_ms("2026-09-21T12:00:00+08:00"),timestamp_ms("2026-09-21T04:00:00Z"))

    def test_naive_shortcut_time_rejected(self):
        with self.assertRaises(ContractError):timestamp_ms("2026-09-21 12:30")

    def test_raw_message(self):
        messages,more,token=parse_messages({"items":[raw_message()],"has_more":False})
        self.assertEqual(messages[0].text,"你好")
        self.assertEqual(messages[0].sender_id,OWNER)

    def test_deleted_message_ignored(self):
        m=raw_message();m["deleted"]=True
        self.assertEqual(parse_messages({"items":[m]})[0],[])

    def test_missing_array_not_empty_conversation(self):
        with self.assertRaises(ContractError):parse_messages({"data":[]})

    def test_image_not_invented(self):
        m=raw_message(kind="image")
        msg=parse_messages({"items":[m]})[0][0]
        self.assertTrue(msg.missing_content);self.assertIn("未解析",msg.text)

    def test_post_extracts_only_text(self):
        m=raw_message(kind="post")
        m["body"]["content"]=json.dumps({"zh_cn":{"content":[[{"tag":"text","text":"内容"},{"tag":"img","image_key":"secret"}]]}})
        msg=parse_messages({"items":[m]})[0][0]
        self.assertIn("内容",msg.text);self.assertIn("未解析",msg.text);self.assertNotIn("secret",msg.text)

    def test_group_speakers_distinct(self):
        ms=[Message("a",OWNER,"user","我说",1),Message("b","ou_alice","user","甲说",2),Message("c","ou_bob","user","乙说",3)]
        state=build_state(ms,cfg())
        roles=[m["speaker"] for m in state["chat"]["messages"]]
        self.assertEqual(roles,["me","participant_1","participant_2"])
        self.assertNotIn("ou_alice",json.dumps(state))

    def test_context_limits(self):
        ms=[Message(str(i),OWNER,"user","x"*100, i) for i in range(40)]
        state=build_state(ms,replace(cfg(),max_context_messages=10,max_context_chars=600))
        self.assertLessEqual(len(state["chat"]["messages"]),6)
        self.assertTrue(state["chat"]["limitations"])

    def test_pasted_me_label(self):
        ms=pasted_messages("我：好\n同事：收到",OWNER)
        self.assertEqual(ms[0].sender_id,OWNER)
        self.assertNotEqual(ms[1].sender_id,OWNER)

    def test_pasted_too_long(self):
        with self.assertRaises(ContractError):pasted_messages("x"*20,OWNER,10)

    def test_group_mention_preserves_addressee(self):
        item=raw_message(sender="ou_colleague",text="@_user_1 你能确认吗？")
        item["mentions"]=[{"id":OWNER,"key":"@_user_1","name":"hidden"}]
        messages,_,_=parse_messages({"items":[item]})
        state=build_state(messages,cfg())
        m=state["chat"]["messages"][0]
        self.assertEqual(m["mentions"],["me"])
        self.assertIn("@me",m["text"])
        self.assertNotIn(OWNER,json.dumps(state))

    def test_bool_timestamp_rejected(self):
        with self.assertRaises(ContractError):timestamp_ms(True)

    def test_post_media_marks_incomplete(self):
        item=raw_message(kind="post")
        item["body"]["content"]=json.dumps({"zh_cn":{"content":[[{"tag":"img","image_key":"img_x"}]]}})
        self.assertTrue(parse_messages({"items":[item]})[0][0].missing_content)

    def test_redaction(self):
        text=clean_text("key sk-1234567890abcdefghijkl 13812345678 test@example.com")
        self.assertNotIn("13812345678",text);self.assertNotIn("test@example.com",text)
        self.assertNotIn("1234567890abcdefghijkl",text)

    def test_disarm_mentions(self):
        self.assertNotIn("<at",outbound_text('<at user_id="all">所有人</at> @all'))

class LarkTests(unittest.TestCase):
    def client(self, responses):
        self.calls=[]
        def runner(args, **kwargs):
            self.calls.append((args, kwargs))
            return SimpleNamespace(returncode=0, stdout=json.dumps(responses.pop(0)), stderr="")
        client=LarkCLI(cfg(),runner)
        client.executable=lambda:"/fake/lark-cli"
        return client

    def test_envelope(self):
        self.assertEqual(unwrap({"ok":True,"identity":"user","data":{"x":1}}),{"x":1})

    def test_nested_envelope(self):
        self.assertEqual(unwrap({"ok":True,"data":{"code":0,"data":{"x":1}}}),{"x":1})

    def test_error_not_success(self):
        with self.assertRaises(LarkError):unwrap({"ok":False,"error":{"secret":"no echo"}})

    def test_raw_api_error(self):
        with self.assertRaises(LarkError):unwrap({"code":99991672,"msg":"token=secret"})

    def test_verify_owner_mismatch(self):
        client=self.client([{"open_id":"ou_someone_else"}])
        with self.assertRaises(PermissionDenied):client.verify_owner()

    def test_unlisted_no_cli_calls(self):
        client=self.client([])
        with self.assertRaises(PermissionDenied):client.read_source("unknown")
        self.assertEqual(self.calls,[])

    def test_source_uses_raw_read_only_api(self):
        client=self.client([{"open_id":OWNER},{"items":[raw_message()],"has_more":False}])
        client.read_source("同事")
        args=self.calls[-1][0]
        self.assertEqual(args[1:4],["api","GET","/open-apis/im/v1/messages"])
        self.assertEqual(args[args.index("--as")+1],"user")
        self.assertFalse(self.calls[-1][1]["shell"])
        params=json.loads(args[args.index("--params")+1]);self.assertEqual(params["container_id"],SOURCE.chat_id)

    def test_user_p2p_read_only_lookup(self):
        client=self.client([{"p2p_chats":[{"chat_id":"oc_resolved"}]},{"items":[],"has_more":False}])
        client._page(identity="user",user_id="ou_colleague")
        args=self.calls[0][0]
        self.assertEqual(args[1:4],["api","POST","/open-apis/im/v1/chat_p2p/batch_query"])
        self.assertEqual(json.loads(args[args.index("--data")+1]),{"chatter_ids":["ou_colleague"]})

    def test_p2p_ambiguous_fails(self):
        client=self.client([{"p2p_chats":[{"chat_id":"oc_one"},{"chat_id":"oc_two"}]}])
        with self.assertRaises(ContractError):client._resolve_p2p("ou_colleague")

    def test_sends_only_to_owner_as_bot(self):
        client=self.client([{"chat_id":CHAT}])
        client.send_owner('hello; $(touch /tmp/noop) --as user',"unique_1")
        args,kw=self.calls[0]
        self.assertEqual(args[args.index("--as")+1],"bot")
        self.assertEqual(args[args.index("--user-id")+1],OWNER)
        self.assertNotIn("--chat-id",args);self.assertFalse(kw["shell"])
        self.assertTrue(any(a.startswith("--text=hello;") for a in args))

    def test_cloud_keys_removed_from_child_env(self):
        client=self.client([{"chat_id":CHAT}])
        with patch.dict(os.environ,{"JEV_API_KEY":"secret_a","LLM_API_KEY":"secret_b"}):client.send_owner("hi","unique")
        self.assertNotIn("JEV_API_KEY",self.calls[0][1]["env"])

    def test_send_disabled(self):
        client=self.client([]);client.cfg=replace(cfg(),allow_bot_send=False)
        with self.assertRaises(PermissionDenied):client.send_owner("hi","test")
        self.assertEqual(self.calls,[])

    def test_inbox_pagination(self):
        client=self.client([{"items":[raw_message("om_new",created="1790000001000")],"has_more":True,"page_token":"next"},
                            {"items":[raw_message("om_old",created="1790000000000")],"has_more":False}])
        result=client.read_inbox(1790000000000)
        self.assertEqual([m.message_id for m in result],["om_old","om_new"])
        self.assertEqual(len(self.calls),2)

    def test_repeated_page_token_fails(self):
        client=self.client([{"items":[],"has_more":True,"page_token":"same"},{"items":[],"has_more":True,"page_token":"same"}])
        with self.assertRaises(ContractError):client.read_inbox(1790000000000)

class ModelTests(unittest.TestCase):
    def setUp(self):
        self.cfg=cfg()
        self.state=build_state(pasted_messages(TRANSCRIPT,OWNER),self.cfg)

    def test_valid_candidates(self):
        response={"choices":[{"message":{"content":json.dumps({"replies":REPLIES},ensure_ascii=False)}}]}
        self.assertEqual(parse_candidates(response),REPLIES)

    def test_no_padding_missing_candidates(self):
        with self.assertRaises(ContractError):parse_candidates({"choices":[{"message":{"content":'["一条"]'}}]})

    def test_repeated_candidates_invalid(self):
        with self.assertRaises(ContractError):parse_candidates({"choices":[{"message":{"content":'["一条","一条","一条"]'}}]})

    def test_demo_judge_contract(self):
        answers,_=DemoClient().judge(self.state)
        self.assertTrue(validate_answers({"answers":answers},judge_questions()))

    def test_demo_rank_contract(self):
        answers,_=DemoClient().rank(self.state,REPLIES)
        self.assertTrue(validate_answers({"answers":answers},ranking_questions(REPLIES)))

    def test_missing_answer_rejected(self):
        with self.assertRaises(ContractError):validate_answers({"answers":{}},judge_questions())

    def test_nan_rejected(self):
        answers,_=DemoClient().judge(self.state);answers["facts_sufficient"]["noul"]=float("nan")
        with self.assertRaises(ContractError):validate_answers({"answers":answers},judge_questions())

    def test_probability_sum_rejected(self):
        answers,_=DemoClient().judge(self.state);answers["likely_request"]["probabilities"]["action"]=0.1
        with self.assertRaises(ContractError):validate_answers({"answers":answers},judge_questions())

    def test_no_default_success_on_judge_failure(self):
        class Client(DemoClient):
            def judge(self,state):raise ProviderError("judge unavailable")
        result=Analyzer(self.cfg,Client(),demo=True).analyze(self.state)
        self.assertFalse(result.judgement);self.assertTrue(result.ranking)
        text=render(result,self.cfg);self.assertIn("分析暂不可用",text)
        self.assertNotIn("可见沟通摩擦：0",text)

    def test_no_ranking_on_bad_generation(self):
        class Client(DemoClient):
            def generate(self,state):raise ContractError("bad candidates")
            def rank(self,*args):raise AssertionError("must not rank")
        result=Analyzer(self.cfg,Client(),demo=True).analyze(self.state)
        self.assertFalse(result.ranking);self.assertIn("candidates",result.errors)

    def test_rank_failure_keeps_unverified_drafts(self):
        class Client(DemoClient):
            def rank(self,*args):raise ProviderError("ranking unavailable")
        text=render(Analyzer(self.cfg,Client(),demo=True).analyze(self.state),self.cfg)
        self.assertIn("未验证草稿",text);self.assertNotIn("本轮相对偏好",text)

    def test_none_option_is_not_forced_recommendation(self):
        result=Analyzer(self.cfg,DemoClient(),demo=True).analyze(self.state)
        result.ranking["best_reply"].update(choice="none",probabilities={"reply_1":.05,"reply_2":.1,"reply_3":.05,"none":.8})
        text=render(result,self.cfg);self.assertIn("不直接采用",text)

    def test_low_grounding_hidden(self):
        result=Analyzer(self.cfg,DemoClient(),demo=True).analyze(self.state)
        for i in range(3):result.ranking[f"grounded_{i+1}"]["noul"]=.1
        text=render(result,self.cfg)
        self.assertIn("均未通过",text)
        for reply in REPLIES:self.assertNotIn(reply,text)

    def test_live_mode_never_uses_demo_without_consent(self):
        with self.assertRaises(PermissionDenied):Analyzer(replace(self.cfg,allow_cloud=False),DemoClient()).analyze(self.state)

    def test_model_client_uses_real_contract(self):
        class Transport:
            def __init__(self):self.calls=[]
            def post(self,url,key,payload):
                self.calls.append(payload)
                if "questions" in payload:
                    answers,_=DemoClient().judge(payload["state"])
                    return {"answers":answers,"usage":{"input_tokens":20}}
                return {"choices":[{"message":{"content":json.dumps({"replies":REPLIES})}}]}
        transport=Transport();client=ModelClient(self.cfg,transport)
        client.judge(self.state);client.generate(self.state)
        self.assertEqual(transport.calls[0]["model"],"typesafe/jev-1.13")
        self.assertEqual(transport.calls[1]["response_format"],{"type":"json_object"})

class TransportTests(unittest.TestCase):
    def opener(self, sequence):
        class Opener:
            calls=0
            def open(self,*args,**kwargs):
                self.calls+=1
                value=sequence.pop(0)
                if isinstance(value,Exception):raise value
                return io.BytesIO(json.dumps(value).encode())
        return Opener()

    def test_401_no_retry_and_no_secret_echo(self):
        opener=self.opener([urllib.error.HTTPError("https://host",401,"secret_xyz",{},None)])
        with self.assertRaises(ProviderError) as cm:JsonTransport(opener=opener).post("https://host","secret",{})
        self.assertEqual(opener.calls,1);self.assertNotIn("secret_xyz",str(cm.exception))

    def test_429_one_bounded_retry(self):
        opener=self.opener([urllib.error.HTTPError("https://host",429,"rate",{"Retry-After":"90"},None),{"ok":True}])
        waits=[];result=JsonTransport(opener=opener,sleeper=waits.append).post("https://host","x",{})
        self.assertTrue(result["ok"]);self.assertEqual(opener.calls,2);self.assertEqual(waits,[5.0])

    def test_redirect_not_retried(self):
        opener=self.opener([urllib.error.HTTPError("https://host",302,"redirect",{},None)])
        with self.assertRaises(ProviderError):JsonTransport(opener=opener).post("https://host","x",{})
        self.assertEqual(opener.calls,1)

    def test_usage_body_not_logged(self):
        opener=self.opener([{"error":{"message":"sk-super-secret"}}])
        with self.assertRaises(ProviderError) as cm:JsonTransport(opener=opener).post("https://host","x",{})
        self.assertNotIn("super-secret",str(cm.exception))

class StoreServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.config=cfg(Path(self.tmp.name))
        self.store=Store(self.config)
        self.keys=patch.dict(os.environ,{"JEV_API_KEY":"synthetic","LLM_API_KEY":"synthetic"});self.keys.start()
    def tearDown(self):
        self.keys.stop();self.store.close();self.tmp.cleanup()

    def test_dedupe_survives_store_reopen(self):
        self.assertTrue(self.store.reserve("message"));self.store.close();self.store=Store(self.config)
        self.assertFalse(self.store.reserve("message"))

    def test_state_no_message_body(self):
        secret="business quote and price 555";self.store.reserve(secret)
        rows=self.store.db.execute("SELECT * FROM requests").fetchall()
        self.assertNotIn(secret,str(rows))

    def test_quota_limits(self):
        self.store.cfg=replace(self.config,per_minute_limit=1)
        self.store.consume_analysis(now=100000)
        with self.assertRaises(CopilotError):self.store.consume_analysis(now=100001)

    def test_daily_quota(self):
        self.store.cfg=replace(self.config,daily_analysis_limit=1)
        self.store.consume_analysis(now=100000)
        with self.assertRaises(CopilotError):self.store.consume_analysis(now=100100)

    def test_binding_rejects_wrong_owner(self):
        save_binding(self.config,{"chat_id":CHAT})
        with self.assertRaises(PermissionDenied):resolve_binding(replace(self.config,owner_open_id="ou_different"))

    def test_binding_resolves_server_chat(self):
        save_binding(self.config,{"chat_id":CHAT})
        self.assertEqual(resolve_binding(replace(self.config,inbox_chat_id="")).inbox_chat_id,CHAT)

    def test_binding_rejects_manual_group_override(self):
        save_binding(self.config,{"chat_id":CHAT})
        with self.assertRaises(PermissionDenied):resolve_binding(replace(self.config,inbox_chat_id="oc_group"))

    def test_unknown_sender_ignored(self):
        class Lark:
            def read_inbox(self,_):return [Message("x","ou_intruder","user","分析 同事",200001)]
            def send_owner(self,*args):raise AssertionError("must not send")
        self.store.set("cursor_ms","200000")
        service=Service(self.config,Lark(),Analyzer(self.config,DemoClient(),demo=True),self.store)
        result=service.once(now=201);self.assertEqual(result["processed"],0)

    def test_owner_id_with_bot_sender_ignored(self):
        class Lark:
            def read_inbox(self,_):return [Message("x",OWNER,"app","分析 同事",200001)]
            def send_owner(self,*args):raise AssertionError("must not send")
        self.store.set("cursor_ms","200000")
        result=Service(self.config,Lark(),Analyzer(self.config,DemoClient(),demo=True),self.store).once(now=201)
        self.assertEqual(result["processed"],0)

    def test_fresh_start_skips_history(self):
        class Lark:
            def read_inbox(self,_):raise AssertionError("first call does not replay history")
        result=Service(self.config,Lark(),None,self.store).once(now=200)
        self.assertTrue(result["initialized"])
        self.assertEqual(self.store.get("initial_ms"),"200000")

    def test_command_parse_no_arbitrary_shell(self):
        self.assertEqual(parse_command("$(rm -rf /)").kind,"help")
        self.assertEqual(parse_command("分析 同事").argument,"同事")

    def test_unknown_alias_before_quota_and_read(self):
        class Lark:
            def read_source(self,*a):raise AssertionError("must not read")
        service=Service(self.config,Lark(),None,self.store)
        with self.assertRaises(PermissionDenied):service.dispatch(parse_command("分析 stranger"))
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM quota").fetchone()[0],0)

    def test_pause_prevents_model_use(self):
        service=Service(self.config,None,None,self.store)
        self.assertIn("暂停",service.dispatch(parse_command("暂停")))
        self.assertIn("暂停",service.dispatch(parse_command("分析 同事")))

    def test_full_service_mocked_end_to_end(self):
        class Lark:
            sent=[]
            def read_inbox(self,_):return [Message("om_command",OWNER,"user","分析 同事",200001)]
            def read_source(self,alias):return pasted_messages(TRANSCRIPT,OWNER),True
            def send_owner(self,text,key):self.sent.append((text,key));return {"chat_id":CHAT}
        lark=Lark();self.store.set("cursor_ms","200000")
        service=Service(self.config,lark,Analyzer(self.config,DemoClient(),demo=True),self.store)
        self.assertEqual(service.once(now=201)["processed"],1)
        self.assertEqual(service.once(now=202)["processed"],0)
        self.assertEqual(len(lark.sent),1)
        self.assertIn("可能诉求",lark.sent[0][0]);self.assertIn("候选回复",lark.sent[0][0])

    def test_send_unknown_does_not_rerun(self):
        class Lark:
            count=0
            def read_inbox(self,_):return [Message("om_command",OWNER,"user","帮助",200001)]
            def send_owner(self,*a):self.count+=1;raise LarkError("uncertain")
        lark=Lark();self.store.set("cursor_ms","200000")
        service=Service(self.config,lark,None,self.store)
        with self.assertRaises(CopilotError):service.once(now=201)
        self.assertEqual(service.once(now=202)["processed"],0);self.assertEqual(lark.count,1)

    def test_single_instance_lock(self):
        with instance_lock(self.config):
            with self.assertRaises(CopilotError):
                with instance_lock(self.config):pass

if __name__ == "__main__":
    unittest.main()
