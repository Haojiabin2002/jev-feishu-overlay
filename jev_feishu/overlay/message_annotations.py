"""Private, read-only annotations anchored to verified visible Feishu messages."""
from pathlib import Path
import json
import math
import os
import queue
import subprocess
import threading
import time
import AppKit
from Foundation import NSMakeRect
from .history import HistoryStore
from .viewmodel import all_decisions


def validated_anchors(payload,allowed):
    if not isinstance(payload,dict) or payload.get('status')!='matched' or not payload.get('chat_verified') or not payload.get('occlusion_checked') or payload.get('timed_out'):
        return []
    valid=[]
    for item in payload.get('anchors',[]):
        if not isinstance(item,dict) or item.get('key') not in allowed or item.get('unoccluded') is not True:continue
        values=[item.get(k) for k in ('x','y','width','height')]
        if not all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) for v in values):continue
        x,y,w,h=values
        if not 60<=w<=500 or not 16<=h<=40 or abs(x)>50000 or abs(y)>50000:continue
        valid.append({k:item[k] for k in ('key','x','y','width','height')})
    return valid


class MessageAnnotations:
    def __init__(self,controller):
        self.c=controller;self.events=queue.Queue();self.panels={};self.buttons={};self.keys=[]
        self.alias='';self.epoch=0;self.busy=False;self.next_poll=0.;self.closed=False
        self.last_reason='等待记录';self.monitor=None
        self.front_pid=None;self.editing=False
        self.summaries={}
        self.monitor=AppKit.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            # Never subscribe to keyboard/IME events in another application.
            AppKit.NSEventMaskScrollWheel|AppKit.NSEventMaskLeftMouseDragged|AppKit.NSEventMaskLeftMouseDown|AppKit.NSEventMaskRightMouseDown,
            lambda event:self.events.put(('moving',None)))

    def hide(self):
        for panel in self.panels.values():panel.orderOut_(None)

    def request_refresh(self):
        # Explicit user refresh can recover after AX-only navigation. The helper
        # still verifies editor focus before reading any message tree.
        self.epoch+=1;self.editing=False;self.next_poll=0;self.hide()

    def close(self):
        self.closed=True;self.epoch+=1
        if self.monitor:AppKit.NSEvent.removeMonitor_(self.monitor)
        for panel in self.panels.values():panel.close()
        self.panels.clear()

    def tick(self):
        if self.closed:return
        if self.monitor is None:
            self.last_reason='滚动监听不可用；暂不显示标注';self.hide();return
        front=AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
        pid=front.processIdentifier() if front else None
        if pid!=self.front_pid:
            self.front_pid=pid;self.epoch+=1;self.hide();self.next_poll=0;self.editing=False
        while True:
            try:kind,data=self.events.get_nowait()
            except queue.Empty:break
            if kind=='moving':
                self.epoch+=1;self.hide();self.next_poll=time.monotonic()+.4;self.editing=False
                self.last_reason='移动或切换中，标注已隐藏'
            elif kind=='result':
                epoch,alias,entries,payload=data;self.busy=False
                if epoch!=self.epoch or alias!=self.c.alias:continue
                self.editing=payload.get('reason')=='editing'
                anchors=validated_anchors(payload,{e['id'] for e in entries})
                shown=self._show(anchors)
                if shown:self.last_reason=f'已显示 {shown} 条本机标注'
                elif payload.get('status')=='matched':
                    reasons=set(payload.get('rejected',{}).values())
                    self.last_reason=('原消息被其他窗口遮挡' if 'covered_by_another_window' in reasons else
                                      '原消息下方空间不足，记录仍可查看' if 'insufficient_gap' in reasons else
                                      '有重复文字，暂不定位' if 'ambiguous' in reasons else
                                      '已分析的原消息不在当前可见位置')
                else:self.last_reason=payload.get('reason','定位不可用')
        if self.c._paused:
            self.hide();self.epoch+=1;return
        if self.alias!=self.c.alias:
            self.alias=self.c.alias;self.epoch+=1;self.hide();self.next_poll=0;self.editing=False
        # Once the native helper sees an editor, stop AX polling until pointer
        # navigation or an app/source change. Do not inspect composition text.
        if self.editing or self.busy or time.monotonic()<self.next_poll:return
        self.next_poll=time.monotonic()+1
        try:
            with HistoryStore(self.c.cfg) as history:entries=history.list(self.alias,100)
        except Exception:
            self.last_reason='记录读取失败';self.hide();return
        # One marker per original message; suppress known repeated-text ambiguity.
        hash_messages={}
        for e in entries:
            if e['anchor_hash']:hash_messages.setdefault(e['anchor_hash'],set()).add(e['message_key'])
        unique=[];seen=set()
        for e in entries:
            if e['message_key'] in seen or not e['anchor_hash'] or len(hash_messages[e['anchor_hash']])!=1:continue
            seen.add(e['message_key']);unique.append(e)
        entries=unique[:40]
        if not entries:
            self.last_reason='暂无可定位的分析记录';self.hide();return
        try:
            with HistoryStore(self.c.cfg) as history:
                for entry in entries:
                    if entry['id'] in self.summaries:continue
                    record=history.get(self.alias,entry['id'])
                    decision=next((d for d in all_decisions(record.result) if d.key=='next_action'),None) if record else None
                    self.summaries[entry['id']]='Jev · '+(decision.value if decision else '分析记录')+' · 查看'
        except Exception:pass
        self.summaries={key:value for key,value in self.summaries.items() if key in {e['id'] for e in entries}}
        helper=(self.c.cfg.base/self.c.settings.message_anchor_helper).resolve()
        if not helper.is_file():
            self.last_reason='消息定位助手未构建';self.hide();return
        source=self.c.cfg.source(self.alias)
        request={'bundle':self.c.settings.follow_bundle_id,'chat_title':source.display_name or self.alias,
                 'overlay_pid':os.getpid(),'ignore_window_ids':[int(p.windowNumber()) for p in self.panels.values()],
                 'anchors':[{'key':e['id'],'hash':e['anchor_hash']} for e in entries]}
        epoch=self.epoch;alias=self.alias;events=self.events;self.busy=True
        def read():
            try:
                env={k:v for k,v in os.environ.items() if k in ('HOME','PATH','LANG','LC_ALL','TMPDIR')}
                p=subprocess.run([str(helper)],input=json.dumps(request),capture_output=True,text=True,timeout=6,env=env)
                payload=json.loads(p.stdout) if p.returncode==0 and len(p.stdout)<200000 else {'reason':'定位读取失败'}
            except Exception:payload={'reason':'定位读取失败'}
            events.put(('result',(epoch,alias,entries,payload)))
        threading.Thread(target=read,name='feishu-message-position',daemon=True).start()

    def _show(self,anchors):
        self.keys=[a['key'] for a in anchors]
        if self.c._paused:
            self.hide();return 0
        flip=self.c._display_height()
        for index,a in enumerate(anchors):
            frame=NSMakeRect(a['x'],flip-a['y']-a['height'],a['width'],a['height'])
            if not any(frame.origin.x>=s.visibleFrame().origin.x and frame.origin.y>=s.visibleFrame().origin.y
                       and frame.origin.x+frame.size.width<=s.visibleFrame().origin.x+s.visibleFrame().size.width
                       and frame.origin.y+frame.size.height<=s.visibleFrame().origin.y+s.visibleFrame().size.height for s in AppKit.NSScreen.screens()):
                if a['key'] in self.panels:self.panels[a['key']].orderOut_(None)
                continue
            key=a['key'];panel=self.panels.get(key)
            if panel is None:
                panel=AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
                    NSMakeRect(0,0,a['width'],a['height']),AppKit.NSWindowStyleMaskBorderless|AppKit.NSWindowStyleMaskNonactivatingPanel,AppKit.NSBackingStoreBuffered,False)
                panel.setLevel_(AppKit.NSFloatingWindowLevel);panel.setHidesOnDeactivate_(False)
                panel.setTitle_('Jev 消息标注');panel.setOpaque_(False)
                panel.setBackgroundColor_(AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(.94,.97,.98,1))
                button=AppKit.NSButton.alloc().initWithFrame_(NSMakeRect(0,0,a['width'],a['height']))
                title=self.summaries.get(key,'Jev · 查看这条消息的分析') if a['width']>=220 else 'Jev · 查看分析' if a['width']>=130 else 'Jev · 记录'
                button.setTitle_(title);button.setBordered_(False)
                button.setFont_(AppKit.NSFont.systemFontOfSize_(11));button.setAlignment_(AppKit.NSTextAlignmentLeft)
                button.setTarget_(self.c);button.setAction_('openMessageHistory:')
                panel.contentView().addSubview_(button);self.panels[key]=panel;self.buttons[key]=button
            self.buttons[key].setTag_(index);self.buttons[key].setFrame_(NSMakeRect(6,0,a['width']-12,a['height']))
            if panel.frame()!=frame:panel.setFrame_display_(frame,True)
            if not panel.isVisible():panel.orderFrontRegardless()
        # Close panels for records that are no longer in the current visible batch.
        for key in list(self.panels):
            if key not in self.keys:self.panels.pop(key).close();self.buttons.pop(key,None)
        return sum(bool(p.isVisible()) for p in self.panels.values())

    def record_for_tag(self,tag):
        if self.alias!=self.c.alias or not 0<=tag<len(self.keys):return None
        key=self.keys[tag]
        return key if key in self.panels and self.panels[key].isVisible() else None
