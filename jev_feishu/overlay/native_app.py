"""Feishu controller around the vendored MIT jev-jarvis AppKit HUD.

The upstream native controls/layout/docking are reused; OCR, local-model fallback,
shared credentials and WeChat input-writing code are intentionally not imported.
"""
from __future__ import annotations
from dataclasses import replace
from datetime import datetime
import copy
from pathlib import Path
import json
import math
import queue
import threading
import time

import objc
import AppKit
import Quartz
from Foundation import NSObject, NSMakeRect, NSMakePoint, NSTimer
from vendor.jev_jarvis import native_ui as ui
from ..config import Config, load_config
from ..models import AnalysisResult
from ..errors import CopilotError
from ..store import instance_lock
from .engine import OverlayEngine, demo_result
from .feishu_provider import ReusedFeishuProvider
from .settings import load_settings, OverlaySettings
from .viewmodel import all_decisions, replies, pct, STAGE_LABELS, STATUS_LABELS, Progress
from .ux import focus_text, readable_context, readiness
from .history import HistoryStore,HistoryRecord


class FeishuHudController(ui.HudController):
    def init(self):
        return objc.super(FeishuHudController,self).init()

    @objc.python_method
    def configure(self,cfg,settings,*,demo=False,headless=False):
        self.cfg=cfg;self.settings=settings;self.demo=demo;self.headless=headless
        self.alias=next((s.alias for s in cfg.sources if s.enabled),'未连接会话')
        if demo:self.alias='合成示例'
        self.engine=OverlayEngine(cfg,lark=ReusedFeishuProvider(cfg))
        self.events=queue.Queue();self.epoch=0;self.closed=False;self.cancel=threading.Event()
        self.result=None;self.capture_signature='';self.progress=Progress()
        self.last_error=''
        self.capture_busy=False;self.analysis_busy=False;self._paused=False
        self.capture_job_epoch=-1;self.analysis_job_epoch=-1
        self.adoption_epoch=-1;self.approved_aliases=set();self.consent_alert=None
        self.auto_analyze=False;self.pending_auto=False;self.analysis_signature='';self.analysis_notice=''
        self.next_read=0.;self.last_read=0.;self._collapsed=False;self._expanded_h=None
        self._last_origin=None;self._pending_origin=None;self._show_boxes=False
        self._dds=[];self._dd_boxes=[];self._rows=[];self._fixed=[];self._title_h=28
        self._group_top=0;self._last_intent='';self.cand_texts=[None]*3
        self.slot_tones=[self.alias];self.shown_replies=[];self.edited={};self.confirmed={};self.actions={}
        self.expanded_details=False;self.expanded_context=False;self._details_control=None;self._document=None
        self.history_record=None;self.history_offset=0;self.demo_history={};self._history_refreshing=False
        self.message_annotations=None
        ui.styles.labels=lambda:([self.alias] if demo else [s.alias for s in cfg.sources if s.enabled]) or [self.alias]
        ui.styles.PRESETS={alias:'' for alias in ui.styles.labels()}
        self._build_panel()
        self.panel.setTitle_('Jev · 飞书对话助手')
        self.panel.standardWindowButton_(AppKit.NSWindowCloseButton).setToolTip_('退出 Jev 飞书助手')
        self._dds[0].removeItemWithTitle_(ui.styles.NONE_LABEL)
        self._dds[0].setToolTip_('只读取这里选定的已授权飞书群')
        self._base_group_top=self._group_top
        # Keep upstream widgets and geometry; adapt the second row action to editing.
        self.edit_checks=[]
        for index,row in enumerate(self._rows[0]):
            row['fill_btn'].setTitle_('修改');row['fill_btn'].setAction_('editCandidate:')
            row['text'].setDelegate_(self);row['text'].setTag_(index)
            check=AppKit.NSButton.alloc().initWithFrame_(NSMakeRect(14,0,76,22))
            check.setButtonType_(AppKit.NSSwitchButton);check.setTitle_('已核对')
            check.setFont_(AppKit.NSFont.systemFontOfSize_(10));check.setTag_(index)
            check.setTarget_(self);check.setAction_('confirmEdited:');check.setHidden_(True)
            self.panel.contentView().addSubview_(check);self.edit_checks.append(check)
        self.read_button=self._make_button(ui.PANEL_W-136,0,54,22,'读取','refreshMessages:',0)
        self.pause_button=self._make_button(ui.PANEL_W-194,0,54,22,'暂停','togglePause:',0)
        self.analyze_button=self._make_button(ui.PANEL_W-78,0,64,22,'分析','reanalyze:',0)
        self.expand_button=self._make_button(ui.PANEL_W-78,0,64,22,'展开','showPanel:',0)
        self.panel.contentView().addSubview_(self.expand_button)
        self.auto_check=AppKit.NSButton.alloc().initWithFrame_(NSMakeRect(0,0,104,22))
        self.auto_check.setButtonType_(AppKit.NSSwitchButton);self.auto_check.setTitle_('自动分析')
        self.auto_check.setFont_(AppKit.NSFont.systemFontOfSize_(10))
        self.auto_check.setTarget_(self);self.auto_check.setAction_('toggleAutoAnalyze:')
        self.auto_check.setToolTip_('只在选定群出现新内容时分析；暂停或切群会关闭。')
        self.auto_check.setEnabled_(not demo)
        self.context_button=self._make_button(ui.PANEL_W-176,0,74,22,'原对话','toggleContext:',0)
        self.details_button=self._make_button(ui.PANEL_W-98,0,84,22,'判断详情','toggleDetails:',0)
        self.history_picker=AppKit.NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(0,0,74,22),True)
        self.history_picker.setFont_(AppKit.NSFont.systemFontOfSize_(11))
        self.history_picker.setTarget_(self);self.history_picker.setAction_('selectHistory:')
        self.history_picker.menu().setDelegate_(self)
        self._extra_buttons=[self.pause_button,self.read_button,self.analyze_button,self.auto_check,self.context_button,self.details_button,self.history_picker]
        for button in self._extra_buttons:
            self.panel.contentView().addSubview_(button);button.setHidden_(False)
        self._fixed=[(c,x,y,(ui.PANEL_W-220 if c==self.rows['chat'] else ui.PANEL_W-140 if c==self.rows['status'] else 170 if c==self.rows['cand_header'] else w),h)
                     for c,x,y,w,h in self._fixed]
        self._fixed.extend([(self.pause_button,ui.PANEL_W-194,25,54,22),
                            (self.read_button,ui.PANEL_W-136,25,54,22),
                            (self.analyze_button,ui.PANEL_W-78,25,64,22),
                            (self.expand_button,ui.PANEL_W-78,25,64,22),
                            (self.auto_check,ui.PANEL_W-118,49,104,22),
                            (self.context_button,ui.PANEL_W-176,self._base_group_top-25,74,24),
                            (self.history_picker,ui.PANEL_W-254,self._base_group_top-25,74,24),
                            (self.details_button,ui.PANEL_W-98,self._base_group_top-25,84,24)])
        self._details_control=self._make_label(14,0,ui.PANEL_W-28,0,size=11,color=ui.PALETTE['text'])
        self._details_control.cell().setWraps_(True)
        self.panel.contentView().addSubview_(self._details_control)
        self._fixed_base=list(self._fixed)
        self._document=self.panel.contentView()
        self.scroll=AppKit.NSScrollView.alloc().initWithFrame_(self._document.frame())
        self.scroll.setHasVerticalScroller_(True);self.scroll.setAutohidesScrollers_(True)
        self.scroll.setDrawsBackground_(True);self.scroll.setBackgroundColor_(ui.PALETTE['bg'])
        self.scroll.setDocumentView_(self._document);self.panel.setContentView_(self.scroll)
        self._relayout()
        self._render('chat',self.alias,ui.PALETTE['green'])
        self._render('status','连接飞书，只读选定群…',ui.PALETTE['muted'])
        self.rows['cand_header'].setStringValue_('回复候选 · 不自动发送')
        self._clear_candidates()
        if demo:
            self.result=demo_result(cfg.base)
            self.demo_history['demo-history']=HistoryRecord('demo-history',self.alias,time.time()-180,'','',
                '上一轮合成分析',copy.deepcopy(self.result),{k:'skipped' for k in self.progress.states})
            self.adoption_epoch=self.epoch
            for stage in self.progress.states:self.progress.update(stage,'skipped')
            self._render_result('合成示例 · 未读取飞书或调用模型')
        self._refresh_history_menu()
        if settings.message_annotations and not demo and not headless:
            from .message_annotations import MessageAnnotations
            self.message_annotations=MessageAnnotations(self)
        return self

    def openMessageHistory_(self,sender):
        if self.message_annotations is None:return
        key=self.message_annotations.record_for_tag(int(sender.tag()))
        if key is None:return
        self._select_history_record(key)
        self.showPanel_(None)

    def showPanel_(self,sender):
        self._sync_collapsed(False)
        if not self.headless:self.panel.orderFrontRegardless()

    def hidePanel_(self,sender):
        self._sync_collapsed(True)

    @objc.python_method
    def _render(self,key,text,color=None):
        if key=='status' and self.history_record is not None:
            text=f'历史记录 · {self.history_record.time_label} · 只读'
        ui.HudController._render(self,key,text,color)

    @objc.python_method
    def _display_result(self):
        return self.history_record.result if self.history_record is not None else self.result

    @objc.python_method
    def _refresh_history_menu(self):
        if self._history_refreshing:return
        self._history_refreshing=True
        try:
            if self.demo:
                entries=[{'id':r.id,'created':r.created,'preview':r.preview} for r in self.demo_history.values()]
            else:
                try:
                    with HistoryStore(self.cfg) as history:entries=history.list(self.alias,31,self.history_offset)
                except Exception:entries=[]
            self.history_picker.removeAllItems()
            self.history_picker.addItemWithTitle_('记录')
            self.history_picker.addItemWithTitle_('返回当前消息')
            self.history_picker.lastItem().setRepresentedObject_('current')
            for entry in entries[:30]:
                stamp=datetime.fromtimestamp(entry['created']).strftime('%m-%d %H:%M:%S')
                self.history_picker.addItemWithTitle_(stamp+' · '+entry['preview'][:32])
                self.history_picker.lastItem().setRepresentedObject_(entry['id'])
            if self.history_offset:
                self.history_picker.addItemWithTitle_('更新的记录…');self.history_picker.lastItem().setRepresentedObject_('page:'+str(max(0,self.history_offset-30)))
            if len(entries)>30:
                self.history_picker.addItemWithTitle_('更早的记录…');self.history_picker.lastItem().setRepresentedObject_('page:'+str(self.history_offset+30))
            if not entries:
                self.history_picker.addItemWithTitle_('暂无分析记录');self.history_picker.lastItem().setEnabled_(False)
        finally:self._history_refreshing=False

    def menuNeedsUpdate_(self,menu):
        if hasattr(self,'history_picker') and menu==self.history_picker.menu():self._refresh_history_menu()

    def selectHistory_(self,sender):
        key=sender.selectedItem().representedObject()
        if key is None:return
        key=str(key)
        if key.startswith('page:'):
            self.history_offset=int(key[5:]);self._refresh_history_menu();return
        self._select_history_record(key)

    @objc.python_method
    def _select_history_record(self,key):
        if key=='current':self.history_record=None
        else:
            if self.demo:record=self.demo_history.get(key)
            else:
                try:
                    with HistoryStore(self.cfg) as history:record=history.get(self.alias,key)
                except Exception:record=None
            if record is None:
                self._render('status','记录无法读取；未切换当前消息',ui.PALETTE['amber']);return
            self.history_record=record
        if self._display_result() is None:
            self.shown_replies=[];self._clear_candidates()
            for field in ('message','sender','intent','confidence','risk','actions'):self._render(field,'')
            self._render('status',self.last_error or '等待读取当前消息',ui.PALETTE['muted'])
        self._render_result('已返回当前消息' if self.history_record is None else '历史记录')
        self._sync_analysis_button()
        self._refresh_details()

    def returnToCurrent_(self,sender):
        self._select_history_record('current')

    @objc.python_method
    def _sync_analysis_button(self):
        historical=self.history_record is not None
        self.analyze_button.setTitle_('回当前' if historical else '分析')
        self.analyze_button.setAction_('returnToCurrent:' if historical else 'reanalyze:')
        self.analyze_button.setEnabled_(historical or (not self._paused and not self.analysis_busy and not self.capture_busy))

    @objc.python_method
    def _install_status_item(self):
        if self.headless:return
        # NSTextField's field editor uses the application's standard Edit actions.
        main=AppKit.NSMenu.alloc().init()
        app_menu=AppKit.NSMenu.alloc().initWithTitle_('Jev')
        app_item=main.addItemWithTitle_action_keyEquivalent_('Jev',None,'')
        app_item.setSubmenu_(app_menu)
        quit_item=app_menu.addItemWithTitle_action_keyEquivalent_('退出 Jev','quitApp:','q')
        quit_item.setTarget_(self)
        edit_menu=AppKit.NSMenu.alloc().initWithTitle_('编辑')
        edit_item=main.addItemWithTitle_action_keyEquivalent_('编辑',None,'')
        edit_item.setSubmenu_(edit_menu)
        for title,action,key in [('撤销','undo:','z'),('剪切','cut:','x'),('复制','copy:','c'),
                                 ('粘贴','paste:','v'),('全选','selectAll:','a')]:
            edit_menu.addItemWithTitle_action_keyEquivalent_(title,action,key)
        AppKit.NSApplication.sharedApplication().setMainMenu_(main)
        self.status_item=AppKit.NSStatusBar.systemStatusBar().statusItemWithLength_(AppKit.NSVariableStatusItemLength)
        self.status_item.button().setTitle_('Jev')
        menu=AppKit.NSMenu.alloc().init()
        for title,action in [('显示助手','showPanel:'),('收起助手','hidePanel:'),('读取当前群','refreshMessages:'),
                             ('分析当前群','reanalyze:'),('暂停 / 恢复读取','togglePause:'),
                             ('自动分析新消息','toggleAutoAnalyze:'),
                             ('原对话','toggleContext:'),('判断详情','toggleDetails:'),('退出','quitApp:')]:
            item=menu.addItemWithTitle_action_keyEquivalent_(title,action,'');item.setTarget_(self)
            if action=='toggleAutoAnalyze:':self.auto_item=item
        self.status_item.setMenu_(menu)

    @objc.python_method
    def _relayout(self):
        if hasattr(self,'_fixed_base'):
            text=self._details_text() if self.expanded_details or self.expanded_context else ''
            # A bounded document height inside a native scroll view; the panel stays on screen.
            self._details_control.setStringValue_(text);self._details_control.setHidden_(not bool(text))
            h=math.ceil(self._details_control.cell().cellSizeForBounds_(NSMakeRect(0,0,ui.PANEL_W-28,100000)).height)+8 if text else 0
            self._fixed=list(self._fixed_base)
            if text:self._fixed.append((self._details_control,14,self._base_group_top,ui.PANEL_W-28,h))
            self._group_top=self._base_group_top+(h+8 if text else 0)
        # Clamp before resizing: never grow past the screen and shrink again on
        # every status tick (which can disturb another app's IME composition).
        if getattr(self,'_document',None) is not None:
            self._panel_height_limit=AppKit.NSScreen.mainScreen().visibleFrame().size.height-36
        ui.HudController._relayout(self)
        if getattr(self,'_document',None) is not None:
            for i,check in enumerate(self.edit_checks):
                f=self._rows[0][i]['prob'].frame();check.setFrame_(NSMakeRect(f.origin.x,f.origin.y,76,22))

    @objc.python_method
    def _details_text(self):
        result=self._display_result()
        if self.expanded_context:
            return readable_context(result,self.alias) if result else '暂无可查看的消息。'
        states=self.history_record.states if self.history_record else self.progress.states
        lines=['历史分析 · 只供查看' if self.history_record else '实际执行状态']+[f'{STAGE_LABELS[k]}：{STATUS_LABELS[v]}' for k,v in states.items()]
        lines += ['会话读取：'+('已暂停' if self._paused else '未暂停')]
        lines += [f'当前来源：{self.alias}','输入框填入：未启用；当前仅支持复制','自动发送：未提供']
        lines += ['自动模型分析：'+('已启用，仅当前群新内容' if self.auto_analyze else '已关闭')]
        if not self.demo:
            source=next((s for s in self.cfg.sources if s.alias==self.alias and s.enabled),None)
            lines += ['模型处理许可：'+('尚未连接' if source is None else '此群持续允许' if source.allow_cloud else '本次打开期间允许' if self.alias in self.approved_aliases else '需确认')]
            if self.message_annotations:
                reasons={'matched':'已启用，仅匹配当前可见原消息','accessibility':'需要本机辅助功能授权',
                         'editing':'输入期间暂隐，点击消息区恢复','focus_unavailable':'无法确认输入状态，暂隐标注',
                         'wrong_chat_or_header_unavailable':'当前飞书聊天与所选来源不匹配','not_visible':'飞书窗口不可见','no_chat_area':'当前不是飞书消息页'}
                lines += ['消息下方标注：'+reasons.get(self.message_annotations.last_reason,self.message_annotations.last_reason)]
        ordered=replies(result,self.cfg.minimum_grounding) if result else []
        labels={r.key:f'回复 {i+1}' for i,r in enumerate(ordered)}
        if self.actions and not self.history_record:lines+=['']+[f'{labels.get(key,"回复")}：{value}' for key,value in self.actions.items()]
        if self.last_error:lines+=['',self.last_error]
        if result:
            for stage,error in result.errors.items():lines.append(f'{STAGE_LABELS.get(stage,stage)}：{error}')
            decisions=all_decisions(result)
            lines+=['',('8项模型判断 · 分值不是任务成功率' if len(decisions)==8 else f'模型判断：已取得 {len(decisions)} / 8 项')]
            if self.edited and not self.history_record:lines+=['以下分值只对应模型原稿；修改稿由你核对，模型未重新评估。','']
            for d in decisions:
                kind={'Choice':'选择判断（Choice）','Score':'评分（Score）','Noul':'是/否判断（Noul）'}[d.kind]
                title=d.title
                if d.key.startswith('grounded_'):
                    title=labels.get('reply_'+d.key.split('_')[-1],'回复')+'：事实和承诺有无依据'
                value=labels.get(d.raw.get('choice'),d.value) if d.key=='best_reply' else d.value
                distribution=' / '.join(f'{labels.get(key,label) if d.key=="best_reply" else label} {p:.0%}' for key,label,p in d.distribution)
                lines += [f'{title} · {kind}：{value}',distribution,
                          '置信度：'+pct(d.confidence) if d.confidence is not None else 'Noul 无单独置信度','']
        return '\n'.join(lines)

    @objc.python_method
    def _refresh_details(self,*,layout=True):
        if not self.expanded_details and not self.expanded_context:return
        if not layout:
            # Transient annotation status must not resize a window while another
            # app is composing text. Full layout follows explicit user actions.
            self._details_control.setStringValue_(self._details_text())
            return
        clip=self.scroll.contentView()
        from_top=self._document.frame().size.height-clip.bounds().origin.y-clip.frame().size.height
        self._relayout()
        if self._collapsed:self._sync_collapsed(True)
        else:
            limit=max(0,self._document.frame().size.height-clip.frame().size.height)
            clip.scrollToPoint_(NSMakePoint(0,min(limit,max(0,limit-from_top))))
            self.scroll.reflectScrolledClipView_(clip)

    @objc.python_method
    def _render_result(self,status=None):
        result=self._display_result()
        if not result:return
        self._render('chat',self.alias,ui.PALETTE['green'])
        self._render('message',focus_text(result),ui.PALETTE['text'])
        self.rows['message'].setToolTip_(focus_text(result))
        count=len(result.state.get('chat',{}).get('messages',[]))
        self._render('sender',f'最近 {count} 条消息 · 仅此群，不跟随切换聊天',ui.PALETTE['muted'])
        decisions={d.key:d for d in all_decisions(result)}
        intent=decisions.get('likely_request');risk=decisions.get('communication_friction');action=decisions.get('next_action')
        self._render('intent',intent.value if intent else '等待模型判断',ui.PALETTE['text'] if intent else ui.PALETTE['muted'])
        self._render('confidence',f'选项 {pct(intent.probability)} · 判断置信度 {pct(intent.confidence)}' if intent else '',ui.PALETTE['muted'])
        risk_label=max(risk.distribution,key=lambda item:item[2])[1] if risk else ''
        self._render('risk',f'{risk_label} · {risk.value}（越高分歧越明显）' if risk else '',ui.PALETTE['text'])
        self._render('actions',action.value+' · 仅建议，未执行' if action else '读取与模型分析相互独立',ui.PALETTE['text'])
        self.shown_replies=replies(result,self.cfg.minimum_grounding)
        items=[{'text':r.text if self.history_record else self.edited.get(r.key,r.text),'prob':r.probability} for r in self.shown_replies]
        self._render_groups([(0,self.alias,items)])
        no_winner=result.ranking.get('best_reply',{}).get('choice')=='none'
        self.rows['cand_header'].setStringValue_('历史回复 · 只供查看' if self.history_record else '均不采用 · 请先修改' if no_winner else '回复候选 · 不自动发送')
        for i,r in enumerate(self.shown_replies):
            row=self._rows[0][i];editing=r.key in self.edited and not self.history_record
            row['text'].setToolTip_(r.text if self.history_record else self.edited.get(r.key,r.text))
            row['text'].setEditable_(editing);row['text'].setBezeled_(editing)
            active=not self.history_record and not self._paused and not self.analysis_busy and not self.capture_busy and self.adoption_epoch==self.epoch
            row['btn'].setEnabled_(active and (editing or (not no_winner and (r.grounded is None or r.grounded>=self.cfg.minimum_grounding))))
            row['fill_btn'].setEnabled_(active)
            if r.probability is None and not self.analysis_busy:row['prob'].setStringValue_(f'#{i+1} · 未核查')
            row['prob'].setHidden_(editing or self._collapsed)
            self.edit_checks[i].setHidden_(not editing or self._collapsed)
            self.edit_checks[i].setState_(AppKit.NSOnState if self.confirmed.get(r.key) else AppKit.NSOffState)
        for i in range(len(self.shown_replies),3):self.edit_checks[i].setHidden_(True)
        if status:self._render('status',status,ui.PALETTE['muted'])
        self._sync_analysis_button()
        if self._collapsed:self._sync_collapsed(True)

    @objc.python_method
    def _invalidate(self):
        self.epoch+=1;self.cancel.set();self.cancel=threading.Event()
        self.adoption_epoch=-1
        self.edited={};self.confirmed={};self.actions={};self.capture_signature=''
        self._clear_candidates()
        for c in self.edit_checks:c.setHidden_(True)
        if self.history_record:self._render_result()

    def toneChanged_(self,sender):
        alias=str(sender.titleOfSelectedItem())
        if alias==self.alias:return
        if not self.demo:self.cfg.source(alias)
        self.history_record=None;self.history_offset=0
        self._invalidate();self.alias=alias;self.slot_tones=[alias];self.result=None
        self._set_auto(False);self.analysis_signature=''
        self.shown_replies=[];self.progress=Progress()
        for key in ('message','sender','intent','confidence','risk','actions'):self._render(key,'')
        self._render('chat',alias,ui.PALETTE['green'])
        self._render('status','已切换来源；等待重新读取',ui.PALETTE['muted']);self.next_read=0
        self._refresh_details()
        self._refresh_history_menu()

    def refreshMessages_(self,sender):
        if self._paused or self.capture_busy or self.analysis_busy:return
        if sender is not None and self.message_annotations:self.message_annotations.request_refresh()
        if self.demo:
            self.result=demo_result(self.cfg.base);self.adoption_epoch=self.epoch
            self.edited={};self.confirmed={};self.actions={}
            self._render_result('合成示例 · 未读取飞书');return
        try:self.cfg.source(self.alias)
        except CopilotError:
            self._render('status','尚未配置已授权飞书群',ui.PALETTE['amber']);return
        self.capture_busy=True;epoch=self.epoch;engine=self.engine;alias=self.alias;cancel=self.cancel;events=self.events
        self.last_error='';self.next_read=time.monotonic()+self.settings.refresh_seconds
        self.capture_job_epoch=epoch
        self.read_button.setEnabled_(False);self.analyze_button.setEnabled_(False)
        for row in self._rows[0]:row['btn'].setEnabled_(False);row['fill_btn'].setEnabled_(False)
        self.progress.update('capture','running');self._render('status','正在读取 '+alias+'…',ui.PALETTE['muted'])
        self._refresh_details()
        def work():
            try:events.put((epoch,'capture',engine.capture(alias=alias,cancelled=cancel)))
            except CopilotError as exc:events.put((epoch,'error',str(exc)))
            except Exception:events.put((epoch,'error','读取失败；未记录消息或凭据。'))
            finally:events.put((epoch,'capture_done',None))
        threading.Thread(target=work,name='feishu-selected-read',daemon=True).start()

    def reanalyze_(self,sender):
        self._begin_analysis(False)

    @objc.python_method
    def _begin_analysis(self,automatic=False):
        if self.demo:self._render_result('合成示例 · 未调用模型');return
        if self._paused or self.analysis_busy or self.capture_busy:return
        if automatic and self.analysis_signature and self.analysis_signature==self.capture_signature:
            self._render('status','没有新消息；未重新调用模型',ui.PALETTE['muted']);return
        try:model_cfg=self.cfg.model_config_for(self.alias)
        except CopilotError:
            self._render('status','尚未连接已授权飞书群',ui.PALETTE['amber']);return
        if not model_cfg.allow_cloud and self.alias not in self.approved_aliases:
            self._render('status','需同意：当前群消息交给 TypeSafe 和 Codex',ui.PALETTE['amber'])
            if self.headless or self.consent_alert is not None:return
            alias=self.alias;epoch=self.epoch
            alert=AppKit.NSAlert.alloc().init();self.consent_alert=alert
            alert.setMessageText_('分析当前飞书群？')
            extra='已选择自动分析：后续仅此群内容变化时继续分析。' if automatic else ''
            alert.setInformativeText_(f'仅将“{alias}”最近最多 {self.cfg.max_context_messages} 条消息，经现有脱敏处理后提交给 TypeSafe Jev 和 OpenAI Codex。不会向飞书发送消息。许可仅用于本次打开的助手。'+extra)
            alert.addButtonWithTitle_('允许并分析');alert.addButtonWithTitle_('取消')
            def confirmed(response):
                self.consent_alert=None
                if response==AppKit.NSAlertFirstButtonReturn and alias==self.alias and epoch==self.epoch:
                    self.approved_aliases.add(alias);self._begin_analysis(automatic)
                elif automatic:self._set_auto(False)
            alert.beginSheetModalForWindow_completionHandler_(self.panel,confirmed)
            return
        if self.alias in self.approved_aliases:model_cfg=replace(model_cfg,allow_cloud=True)
        try:model_cfg.check_models();model_cfg.source(self.alias)
        except CopilotError as exc:
            self._render('status',str(exc),ui.PALETTE['amber']);return
        self._invalidate();self.analysis_busy=True;self.progress=Progress()
        self.read_button.setEnabled_(False);self.analyze_button.setEnabled_(False)
        self.last_error='';self.analysis_notice=''
        self.analysis_job_epoch=self.epoch
        epoch=self.epoch;engine=OverlayEngine(model_cfg,lark=ReusedFeishuProvider(model_cfg));alias=self.alias;cancel=self.cancel;events=self.events
        previous=self.analysis_signature
        self._render('status','读取 → Jev 判断 / Codex 生成 → 排序…',ui.PALETTE['muted'])
        def emit(stage,status,snapshot):events.put((epoch,'progress',(stage,status,snapshot)))
        def work():
            try:events.put((epoch,'analysis',engine.run(alias=alias,automatic=automatic,previous=previous,cancelled=cancel,on_event=emit)))
            except CopilotError as exc:events.put((epoch,'error',str(exc)))
            except Exception:events.put((epoch,'error','分析失败；未使用示例结果。'))
            finally:events.put((epoch,'analysis_done',None))
        threading.Thread(target=work,name='feishu-model-analysis',daemon=True).start()

    def copyCandidate_(self,sender):
        i=int(sender.tag())
        if self.history_record or self._paused or self.analysis_busy or self.capture_busy or self.adoption_epoch!=self.epoch or not 0<=i<len(self.shown_replies):return
        r=self.shown_replies[i];edited=r.key in self.edited
        if not edited and self.result.ranking.get('best_reply',{}).get('choice')=='none':return
        if edited and not self.confirmed.get(r.key):
            self._render('status','修改稿：请先勾选“已核对”',ui.PALETTE['amber']);return
        if not edited and r.grounded is not None and r.grounded<self.cfg.minimum_grounding:return
        text=str(self._rows[0][i]['text'].stringValue()) if edited else r.text
        if not 1<=len(text)<=500 or '\0' in text:
            self._render('status','回复须为1–500字',ui.PALETTE['amber']);return
        try:
            pb=getattr(self,'test_pasteboard',None) or AppKit.NSPasteboard.generalPasteboard()
            pb.clearContents();pb.setString_forType_(text,AppKit.NSPasteboardTypeString)
            ok=pb.stringForType_(AppKit.NSPasteboardTypeString)==text
        except Exception:ok=False
        if edited:self.edited[r.key]=text
        self.actions[r.key]='已复制修改稿，模型未重新评估' if edited and ok else '已复制，未发送' if ok else '复制未确认'
        self._render('status',self.actions[r.key],ui.PALETTE['green'] if ok else ui.PALETTE['amber'])
        self._refresh_details()

    def editCandidate_(self,sender):
        i=int(sender.tag())
        if self.history_record or self._paused or self.analysis_busy or self.capture_busy or self.adoption_epoch!=self.epoch or not 0<=i<len(self.shown_replies):return
        r=self.shown_replies[i];self.edited.setdefault(r.key,r.text);self.confirmed[r.key]=False
        self._render_result('修改稿由你核对，模型未重新评估；仅复制，不填入。')
        self.panel.makeFirstResponder_(self._rows[0][i]['text'])

    def controlTextDidChange_(self,notification):
        if self.history_record:return
        i=int(notification.object().tag())
        if not 0<=i<len(self.shown_replies):return
        r=self.shown_replies[i];self.edited[r.key]=str(notification.object().stringValue())
        self.confirmed[r.key]=False;self.edit_checks[i].setState_(AppKit.NSOffState)

    def confirmEdited_(self,sender):
        i=int(sender.tag())
        if 0<=i<len(self.shown_replies):self.confirmed[self.shown_replies[i].key]=bool(sender.state())

    def toggleDetails_(self,sender):
        self._toggle_expansion('details')

    def toggleContext_(self,sender):
        self._toggle_expansion('context')

    @objc.python_method
    def _toggle_expansion(self,kind):
        self.expanded_details=not self.expanded_details if kind=='details' else False
        self.expanded_context=not self.expanded_context if kind=='context' else False
        self.details_button.setTitle_('收起判断' if self.expanded_details else '判断详情')
        self.context_button.setTitle_('收起对话' if self.expanded_context else '原对话')
        self._relayout()
        self.scroll.contentView().scrollToPoint_(NSMakePoint(0,max(0,self._document.frame().size.height-self.scroll.contentView().frame().size.height)))
        self.scroll.reflectScrolledClipView_(self.scroll.contentView())
        if self._collapsed:self._sync_collapsed(True)

    def collapsePanel_(self,sender):
        self._sync_collapsed(not self._collapsed)

    @objc.python_method
    def _sync_collapsed(self,collapsed):
        self._set_collapsed(collapsed)
        for c in self._extra_buttons:c.setHidden_(self._collapsed)
        self.expand_button.setHidden_(not self._collapsed)
        if self._details_control:self._details_control.setHidden_(self._collapsed or not (self.expanded_details or self.expanded_context))
        for i,c in enumerate(self.edit_checks):c.setHidden_(self._collapsed or bool(self.history_record) or i>=len(self.shown_replies) or self.shown_replies[i].key not in self.edited)
        for i,row in enumerate(self._rows[0]):
            row['prob'].setHidden_(self._collapsed or i>=len(self.shown_replies) or (not self.history_record and self.shown_replies[i].key in self.edited))
        self.scroll.contentView().scrollToPoint_(NSMakePoint(0,max(0,self._document.frame().size.height-self.scroll.contentView().frame().size.height)))
        self.scroll.reflectScrolledClipView_(self.scroll.contentView())

    def togglePause_(self,sender):
        self._paused=not self._paused;self._invalidate()
        if self._paused:self._set_auto(False)
        if self._paused:
            for stage,status in self.progress.states.items():
                if status in ('waiting','running'):self.progress.update(stage,'cancelled')
        resume='已恢复；点“读取”重新载入示例' if self.demo else '已恢复；正在读取当前群最新消息'
        self._render('status','已暂停；旧回复不可采用' if self._paused else resume,ui.PALETTE['muted'])
        self.pause_button.setTitle_('恢复' if self._paused else '暂停')
        ready=not self._paused and not self.capture_busy and not self.analysis_busy
        self.read_button.setEnabled_(ready);self._sync_analysis_button()
        if not self._paused:self.next_read=0
        self._refresh_details()

    @objc.python_method
    def _set_auto(self,value):
        self.auto_analyze=bool(value);self.pending_auto=False
        if hasattr(self,'auto_item'):self.auto_item.setState_(AppKit.NSOnState if value else AppKit.NSOffState)
        if hasattr(self,'auto_check'):self.auto_check.setState_(AppKit.NSOnState if value else AppKit.NSOffState)

    def toggleAutoAnalyze_(self,sender):
        if self.demo:
            self._render('status','合成示例不自动调用模型',ui.PALETTE['muted']);return
        if self._paused:
            self._set_auto(False)
            self._render('status','已暂停；先恢复读取',ui.PALETTE['muted']);return
        self._set_auto(not self.auto_analyze)
        if self.auto_analyze:self.pending_auto=True;self.next_read=0
        self._refresh_details()

    @objc.python_method
    def _read_status(self):
        count=len(self.result.state.get('chat',{}).get('messages',[])) if self.result else 0
        if self.result and self.result.errors:return f'已读 {count} 条 · 部分分析失败，见详情'
        if self.result and self.result.ranking:return f'已读 {count} 条 · 分析已完成'
        mode='自动分析已开启' if self.auto_analyze else '点“分析”生成建议'
        return f'已读 {count} 条 · {mode}'

    @objc.python_method
    def _target_window(self):
        apps=AppKit.NSRunningApplication.runningApplicationsWithBundleIdentifier_(self.settings.follow_bundle_id)
        if not apps:return None
        pids={a.processIdentifier() for a in apps}
        wins=Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly,Quartz.kCGNullWindowID) or []
        matches=[w for w in wins if w.get('kCGWindowOwnerPID') in pids and w.get('kCGWindowLayer')==0]
        matches=[w for w in matches if w.get('kCGWindowBounds',{}).get('Width',0)>500 and w.get('kCGWindowBounds',{}).get('Height',0)>350]
        if not matches:return None
        w=max(matches,key=lambda w:w['kCGWindowBounds']['Width']*w['kCGWindowBounds']['Height'])['kCGWindowBounds']
        return {'x':w['X'],'y':w['Y'],'w':w['Width'],'h':w['Height']}

    def tick_(self,timer):
        if self.closed:return
        if self.message_annotations:
            previous=self.message_annotations.last_reason
            self.message_annotations.tick()
            if self.message_annotations.last_reason!=previous:self._refresh_details(layout=False)
        for _ in range(40):
            try:epoch,kind,value=self.events.get_nowait()
            except queue.Empty:break
            if kind=='capture_done' and epoch==self.capture_job_epoch:self.capture_busy=False
            if kind=='analysis_done' and epoch==self.analysis_job_epoch:self.analysis_busy=False
            if kind in ('capture_done','analysis_done'):
                ready=not self._paused and not self.capture_busy and not self.analysis_busy
                self.read_button.setEnabled_(ready);self._sync_analysis_button()
                if epoch!=self.epoch and not self._paused and self.result is None:self.next_read=0
            if epoch!=self.epoch:continue
            if kind=='capture':
                self.last_read=value.read_at;self.progress.update('capture','done')
                if value.signature!=self.capture_signature or self.result is None:
                    self.epoch+=1;self.cancel.set();self.cancel=threading.Event();self.edited={};self.confirmed={};self.actions={}
                    self.capture_signature=value.signature;self.result=value.result
                    self.adoption_epoch=-1
                    for stage in ('judgement','candidates','ranking'):self.progress.update(stage,'skipped')
                    self._render_result(self._read_status())
                    if self.auto_analyze:self.pending_auto=True
                else:self._render('status',self._read_status(),ui.PALETTE['muted'])
            elif kind=='progress':
                stage,status,snapshot=value;self.progress.update(stage,status)
                if snapshot is not None:self.result=snapshot;self._render_result('模型处理中 · 尚未发送')
            elif kind=='analysis':
                self._refresh_history_menu()
                if value.history_error:self.last_error=value.history_error
                self.capture_signature=value.signature;self.last_read=value.read_at
                if value.result is None:
                    self.analysis_notice=value.notice;self.adoption_epoch=-1
                    for stage in ('judgement','candidates','ranking'):self.progress.update(stage,'skipped')
                    self._render('status',value.notice,ui.PALETTE['muted'])
                else:
                    self.result=value.result
                    if not value.result.errors:self.analysis_signature=value.signature
                    self._render_result('分析完成 · 未发送' if not value.result.errors else '部分模型步骤失败，请展开详情')
            elif kind=='analysis_done' and self.result:
                if not self.analysis_notice:self.adoption_epoch=self.epoch
                self._render_result(self.analysis_notice or ('分析完成 · 未发送' if not self.result.errors else '部分模型步骤失败'))
            elif kind=='capture_done' and self.result:self._render_result()
            elif kind=='error':
                self.last_error=value;self.adoption_epoch=-1;self.result=None;self.shown_replies=[]
                self._clear_candidates()
                for check in self.edit_checks:check.setHidden_(True)
                for key in ('message','sender','intent','confidence','risk','actions'):self._render(key,'')
                for stage,status in self.progress.states.items():
                    if status=='running':self.progress.update(stage,'failed')
                    elif status=='waiting':self.progress.update(stage,'skipped')
                self._render('status',value,ui.PALETTE['amber'])
                if self.history_record:self._render_result()
            self._refresh_details()
        if self.settings.follow_window and not self.headless:self._position_near(self._target_window())
        if not self.demo and not self._paused and time.monotonic()>=self.next_read:
            self.next_read=time.monotonic()+self.settings.refresh_seconds;self.refreshMessages_(None)
        if self.pending_auto and self.result is not None and not self._paused and not self.capture_busy and not self.analysis_busy:
            self.pending_auto=False;self._begin_analysis(True)

    def quitApp_(self,sender):
        self.closed=True;self.cancel.set();self.epoch+=1
        if self.message_annotations:self.message_annotations.close()
        if hasattr(self,'timer'):self.timer.invalidate()
        self.panel.orderOut_(None)
        AppKit.NSApplication.sharedApplication().terminate_(None)


def launch(config_path,*,demo=False):
    path=Path(config_path).resolve()
    cfg=Config(base=path.parent) if demo else load_config(path)
    settings=OverlaySettings() if demo else load_settings(path.parent/'overlay.toml')
    def run():
        app=AppKit.NSApplication.sharedApplication();app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
        controller=FeishuHudController.alloc().init().configure(cfg,settings,demo=demo)
        controller._position_near(None);controller._show()
        controller.timer=NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(.25,controller,'tick:',None,True)
        app.run();return 0
    if demo:return run()
    with instance_lock(cfg):return run()
