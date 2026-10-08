#!/usr/bin/env python3
"""Synthetic CLI protocol double used only by the subprocess integration tests."""
import json
import os
from pathlib import Path
import sys

args=sys.argv[1:]
fixture=Path(os.environ["FAKE_LARK_FIXTURE"])
scenario=json.loads(fixture.read_text())
identity=args[args.index("--as")+1] if "--as" in args else "user"
record={"argv":args,"model_secret_present":any(os.environ.get(k) for k in ("JEV_API_KEY","LLM_API_KEY"))}
with Path(os.environ["FAKE_LARK_LOG"]).open("a") as f:f.write(json.dumps(record,ensure_ascii=False)+"\n")
if args[:3]==["api","GET","/open-apis/authen/v1/user_info"]:
    data={"open_id":scenario["owner"]}
elif args[:3]==["api","GET","/open-apis/im/v1/messages"]:
    params=json.loads(args[args.index("--params")+1]);chat=params["container_id"]
    data={"items":scenario["chats"].get(chat,[]),"has_more":False}
elif args[:3]==["api","POST","/open-apis/im/v1/chat_p2p/batch_query"]:
    data={"p2p_chats":[{"chat_id":scenario["source_chat"]}]}
elif args[:2]==["im","+messages-send"]:
    if identity!="bot" or args[args.index("--user-id")+1]!=scenario["owner"]:
        raise SystemExit("forbidden destination")
    data={"chat_id":scenario["inbox"],"message_id":"om_synthetic_reply"}
else:
    raise SystemExit("unexpected command")
print(json.dumps({"ok":True,"identity":identity,"data":{"code":0,"data":data}},ensure_ascii=False))
