#!/usr/bin/env python3
"""Read one approved source and check native positions without logging message text."""
from pathlib import Path
import argparse,json,os,subprocess,sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from jev_feishu.config import load_config
from jev_feishu.overlay.feishu_provider import ReusedFeishuProvider
from jev_feishu.overlay.history import anchor_hash,anchor_normal

p=argparse.ArgumentParser();p.add_argument('--alias',required=True);p.add_argument('--label',default='current');args=p.parse_args()
if not args.label.replace('-','').isalnum():raise SystemExit('invalid evidence label')
cfg=load_config(ROOT/'config.toml');source=cfg.source(args.alias)
messages,_=ReusedFeishuProvider(cfg).read_source(args.alias)
anchors=[{'key':str(i),'hash':anchor_hash(m.text)} for i,m in enumerate(messages) if len(anchor_normal(m.text))>=6]
env={k:v for k,v in os.environ.items() if k in ('HOME','PATH','LANG','LC_ALL','TMPDIR')}
response=subprocess.run([str(ROOT/'native/jev-message-anchors')],input=json.dumps({'bundle':'com.electron.lark','chat_title':source.display_name or args.alias,'anchors':anchors}),text=True,capture_output=True,timeout=8,env=env)
payload=json.loads(response.stdout)
(ROOT/'docs/tests/local').mkdir(parents=True,exist_ok=True)
(ROOT/'docs/tests/local'/f'message-anchors-{args.label}.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'status':payload.get('status'),'reason':payload.get('reason'),'chat_verified':payload.get('chat_verified'),'foreground':payload.get('foreground'),'matched_count':len(payload.get('anchors',[])),'rejection_counts':{v:list(payload.get('rejected',{}).values()).count(v) for v in set(payload.get('rejected',{}).values())},'timed_out':payload.get('timed_out')},ensure_ascii=False))
