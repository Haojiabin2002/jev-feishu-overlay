"""Bridge to the MIT work-with-jev provider, using the existing user CLI identity."""
from pathlib import Path
import json
import os
import subprocess
from ..lark import LarkCLI, LarkError
from ..messages import parse_messages


class ReusedFeishuProvider(LarkCLI):
    def __init__(self,cfg,**kwargs):
        super().__init__(cfg,**kwargs)
        self.display_names={}

    def read_source(self,alias):
        source=self.cfg.source(alias)
        self.verify_owner()
        chat_id=source.chat_id or self._resolve_p2p(source.user_id)
        payload={'cli':self.executable(),'chat':{'id':chat_id,'name':source.alias},'count':self.cfg.max_context_messages}
        env={k:v for k,v in os.environ.items() if k in ('HOME','PATH','LANG','LC_ALL','TMPDIR')}
        try:
            cp=subprocess.run([self.cfg.node_binary,str(Path(__file__).resolve().parents[2]/'sdk/feishu-read.mjs')],
                input=json.dumps(payload),capture_output=True,text=True,timeout=self.cfg.cli_timeout+10,env=env,shell=False)
            if cp.returncode or len(cp.stdout)>2_000_000:raise ValueError()
            data=json.loads(cp.stdout)
            messages,more,_=parse_messages(data)
            self.display_names=data.get('display_names',{})
            return messages,more
        except (OSError,subprocess.TimeoutExpired,ValueError,TypeError):
            raise LarkError('飞书读取失败；请检查已有 CLI 的用户登录、指定会话权限与网络。') from None
