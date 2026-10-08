// Reuse work-with-jev's provider. The runner preserves raw millisecond timestamps.
import { createFeishuProvider } from '../vendor/work_with_jev/lib/providers/feishu.mjs';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
const run=promisify(execFile);
try {
  let text='';for await(const chunk of process.stdin){text+=chunk;if(text.length>10000)throw Error('limit');}
  const input=JSON.parse(text);const raw=new Map();let actualMore=false;
  const provider=createFeishuProvider(async args=>{
    if(args[0]!=='im'||args[1]!=='+chat-messages-list'||args[args.indexOf('--as')+1]!=='user')throw Error('read_only');
    const value=flag=>args.includes(flag)?args[args.indexOf(flag)+1]:undefined;
    if(value('--chat-id')!==input.chat.id)throw Error('wrong_source');
    const remaining=input.count-raw.size;
    if(remaining<=0)return {ok:true,data:{items:[],has_more:false}};
    const params={container_id_type:'chat',container_id:input.chat.id,sort_type:'ByCreateTimeDesc',page_size:Math.min(remaining,Number(value('--page-size')))};
    if(value('--page-token'))params.page_token=value('--page-token');
    const env=Object.fromEntries(Object.entries(process.env).filter(([key])=>['HOME','PATH','LANG','LC_ALL','TMPDIR'].includes(key)));
    const {stdout}=await run(input.cli,['api','GET','/open-apis/im/v1/messages','--as','user','--params',JSON.stringify(params),'--format','json'],
      {timeout:30000,maxBuffer:2000000,env:{...env,LARKSUITE_CLI_NO_UPDATE_NOTIFIER:'1',LARKSUITE_CLI_NO_SKILLS_NOTIFIER:'1'}});
    let payload=JSON.parse(stdout);
    if(payload.ok===false)throw Error('cli_failed');
    payload=payload.data??payload;
    if('code' in payload){if(payload.code!==0)throw Error('api_failed');payload=payload.data;}
    if(!Array.isArray(payload.items))throw Error('bad_contract');
    actualMore=!!payload.has_more;
    for(const message of payload.items)raw.set(message.message_id,message);
    return {ok:true,data:payload};
  });
  const result=await provider.messages({chat:input.chat,count:input.count});
  const ids=new Set(result.messages.map(m=>m.id));
  process.stdout.write(JSON.stringify({items:[...raw.values()].filter(m=>ids.has(m.message_id)),has_more:actualMore,raw_count:raw.size,
    display_names:Object.fromEntries(result.messages.map(m=>[m.senderId,m.sender]))}));
} catch {process.stderr.write('Feishu source read failed; credentials and messages were not logged.\n');process.exitCode=1;}
