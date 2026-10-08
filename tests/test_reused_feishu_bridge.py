from pathlib import Path
import json,os,shlex,subprocess,sys,tempfile,unittest

ROOT=Path(__file__).resolve().parents[1]

class ReusedBridgeTests(unittest.TestCase):
    def test_raw_limit_no_extra_history_and_no_model_secrets_in_cli(self):
        with tempfile.TemporaryDirectory(prefix='Jev 测试 ') as tmp:
            base=Path(tmp);log=base/'calls.jsonl';script=base/'cli.py';launcher=base/'lark-cli'
            script.write_text('''import json,sys,os
from pathlib import Path
assert sys.argv[1:4]==['api','GET','/open-apis/im/v1/messages']
params=json.loads(sys.argv[sys.argv.index('--params')+1])
record={'identity':sys.argv[sys.argv.index('--as')+1],'size':params['page_size'],'secret_present':any(os.environ.get(k) for k in ('JEV_API_KEY','UNRELATED_SECRET'))}
with Path('''+repr(str(log))+''').open('a') as f:f.write(json.dumps(record)+'\\n')
rows=[{'message_id':'m'+str(i),'msg_type':'system' if i in (0,15) else 'text','sender':{'id':'ou_other','sender_type':'user'},'create_time':str(1790000000000+i),'body':{'content':json.dumps({'text':'合成办公消息'})}} for i in range(params['page_size']-1,-1,-1)]
print(json.dumps({'ok':True,'data':{'code':0,'data':{'items':rows,'has_more':True,'page_token':'older'}}}))
''')
            launcher.write_text('#!/bin/sh\nexec '+shlex.quote(sys.executable)+' '+shlex.quote(str(script))+' "$@"\n');launcher.chmod(0o700)
            payload={'cli':str(launcher),'chat':{'id':'oc_testgroup','name':'合成群'},'count':30}
            env={**os.environ,'JEV_API_KEY':'synthetic-only','UNRELATED_SECRET':'synthetic-only'}
            cp=subprocess.run(['node',str(ROOT/'sdk/feishu-read.mjs')],input=json.dumps(payload),text=True,capture_output=True,env=env,timeout=10)
            self.assertEqual(cp.returncode,0,cp.stderr)
            value=json.loads(cp.stdout);calls=[json.loads(x) for x in log.read_text().splitlines()]
            self.assertEqual(len(calls),1);self.assertEqual(calls[0],{'identity':'user','size':30,'secret_present':False})
            self.assertEqual(value['raw_count'],30);self.assertEqual(len(value['items']),28);self.assertTrue(value['has_more'])
            self.assertEqual(value['items'][0]['create_time'],'1790000000029')

    def test_invalid_group_does_not_execute_cli(self):
        payload={'cli':'/must-not-execute','chat':{'id':'invalid','name':'测试'},'count':30}
        cp=subprocess.run(['node',str(ROOT/'sdk/feishu-read.mjs')],input=json.dumps(payload),text=True,capture_output=True,timeout=10)
        self.assertNotEqual(cp.returncode,0);self.assertEqual(cp.stdout,'')
