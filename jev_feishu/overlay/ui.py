"""Tk floating panel; all widget/clipboard mutations stay on the main thread.

The model workers communicate through a bounded event queue. A generation number
prevents late results from an old conversation appearing under the new selector.
"""
from __future__ import annotations
from dataclasses import replace
from pathlib import Path
import json
import gc
import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
from tkinter import font as tkfont

from ..config import Config, load_config
from ..errors import CopilotError
from ..models import AnalysisResult
from ..store import instance_lock
from .engine import OverlayEngine, Outcome, demo_result
from .input_bridge import InputBridge, FillResult
from .settings import OverlaySettings, load_settings
from .viewmodel import (Decision, Reply, Progress, TITLES, STAGE_LABELS, STATUS_LABELS,
                        all_decisions, replies, pct)
from .ux import (focus_text, readable_context, readiness, friendly_error, friction_label,
                 draft_badge, fill_explanation, next_step_text, TYPE_LABELS)
from .preferences import load_preferences, save_preferences
from .compact import decision_annotation
from .window_follow import WindowFollower, anchor_position

BG="#F3F4F5"; PAPER="#FFFFFF"; INK="#27313B"; MUTED="#596570"; BLUE="#24795A"
BLUE_BG="#EDF6F0"; GREEN="#24795A"; GREEN_BG="#EDF6F0"; AMBER="#90621E"; AMBER_BG="#FBF3E5"
BORDER="#DFE3E6"; TRACK="#EAEEF0"; RED="#AD4545"

class Card(tk.Canvas):
    def __init__(self,parent,*,fill=PAPER,pad=14):
        super().__init__(parent,bg=parent.cget("bg"),highlightthickness=0,bd=0,height=60)
        self.color,self.pad=fill,pad
        self.body=tk.Frame(self,bg=fill)
        self.win=self.create_window(pad,pad,window=self.body,anchor="nw")
        self.bind("<Configure>",self._size)
        self.body.bind("<Configure>",self._body)
    def _body(self,event):
        h=self.body.winfo_reqheight()+2*self.pad
        if abs(h-self.winfo_height())>1:self.configure(height=h)
    def _size(self,event):
        w,h=event.width,event.height; r=17
        self.itemconfigure(self.win,width=max(100,w-2*self.pad))
        self.delete("surface")
        pts=[r,0,w-r,0,w,0,w,r,w,h-r,w,h,w-r,h,r,h,0,h,0,h-r,0,r,0,0]
        self.create_polygon(pts,smooth=True,splinesteps=24,fill=self.color,outline=BORDER if self.color==PAPER else self.color,tags="surface")
        self.tag_lower("surface")

class ScrollArea(tk.Frame):
    def __init__(self,parent):
        super().__init__(parent,bg=BG)
        self.canvas=tk.Canvas(self,bg=BG,highlightthickness=0,bd=0)
        self.bar=ttk.Scrollbar(self,orient="vertical",command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.bar.pack(side="right",fill="y");self.canvas.pack(side="left",fill="both",expand=True)
        self.body=tk.Frame(self.canvas,bg=BG)
        self.window=self.canvas.create_window(0,0,window=self.body,anchor="nw")
        self.body.bind("<Configure>",lambda e:self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>",lambda e:self.canvas.itemconfigure(self.window,width=e.width))
    def clear(self):
        for child in self.body.winfo_children():child.destroy()
    def scroll(self,delta):self.canvas.yview_scroll(delta,"units")

class OverlayApp:
    def __init__(self,root: tk.Tk,cfg: Config,settings: OverlaySettings,*,demo=False,engine=None,bridge=None):
        self.root,self.cfg,self.settings,self.demo=root,cfg,settings,demo
        self.engine=engine or OverlayEngine(cfg)
        self.bridge=bridge or InputBridge(cfg.base,settings)
        self.result: AnalysisResult|None=None
        self.result_alias=""
        self.result_at=0.0;self.read_at=0.0;self.result_revision=0
        self.signatures={}
        self.action_states:dict[str,str]={}
        self.edited_drafts:dict[str,str]={}
        self.events=queue.Queue(maxsize=80)
        self.worker:threading.Thread|None=None
        self.stop=threading.Event();self.epoch=0
        self.busy=False;self.filling=False;self.fill_started=False;self.fill_key="";self.closed=False;self.collapsed=False
        self.worker_epoch=-1
        self.progress=Progress();self.notice="先粘贴一段对话，或选择已连接的飞书聊天。"
        self.tab="建议";self.poll_count=0;self.attempt_count=0
        self.auto=tk.BooleanVar(value=False)
        self.alias=tk.StringVar(value="手动文本")
        self.opacity=tk.DoubleVar(value=settings.opacity)
        self.follow_enabled=tk.BooleanVar(value=settings.follow_window)
        self.follower=WindowFollower(cfg.base,settings)
        self.follow_offset=None;self.follow_bounds=None;self._dragging=False
        self.text_input="";self.job_alias=""
        self.show_alternatives=False;self.result_stale=False;self.last_error=""
        self.show_runtime=False;self.show_raw=False;self.show_context=False
        self.show_distribution=False
        self.copy_buttons={};self.fill_buttons={};self.edit_buttons={}
        self.editor=None;self.text_editor=None;self.settings_window=None
        self._after_ids=[];self._countdown_id=None
        self._setup_root()
        self._build()
        if demo:self.load_demo()
        self._schedule(80,self._drain)
        self._schedule(settings.refresh_seconds*1000,self._auto_tick)
        self._schedule(10000,self._freshness_tick)
        if self.follow_enabled.get():self.follower.start()
        self._schedule(250,self._follow_tick)

    def _schedule(self,ms,fn):
        if not self.closed:
            ident=self.root.after(ms,fn);self._after_ids.append(ident)
            if len(self._after_ids)>100:self._after_ids=self._after_ids[-100:]
            return ident

    def _setup_root(self):
        root=self.root
        root.title("Jev · 飞书悬浮助手")
        root.configure(bg=BG)
        root.overrideredirect(True)
        root.attributes("-topmost",True)
        root.attributes("-alpha",self.settings.opacity)
        width=self.settings.width;height=min(self.settings.height,root.winfo_screenheight()-60)
        x=max(10,root.winfo_screenwidth()-width-36);y=24
        root.geometry(f"{width}x{height}+{x}+{y}")
        root.minsize(440,580)
        families=set(tkfont.families(root))
        family=next((x for x in ("PingFang SC","Noto Sans CJK SC","Microsoft YaHei","Arial") if x in families),"TkDefaultFont")
        self.family=family
        self.font=(family,11);self.small=(family,10);self.bold=(family,11,"bold")
        root.option_add("*Font",self.font)
        style=ttk.Style(root)
        if "clam" in style.theme_names():style.theme_use("clam")
        style.configure("TCombobox",padding=6,fieldbackground=PAPER,foreground=INK,arrowsize=12)
        style.map("TCombobox",fieldbackground=[("readonly",PAPER)],foreground=[("readonly",INK)])
        style.configure("TScrollbar",background="#CDD6E7",troughcolor=BG,borderwidth=0,arrowsize=10)
        style.configure("Jev.TButton",font=self.font,padding=(9,5),background=PAPER,foreground=INK,borderwidth=0)
        style.map("Jev.TButton",background=[("active",BLUE_BG)],foreground=[("disabled",MUTED)])
        style.configure("Primary.TButton",font=self.bold,padding=(11,5),background=BLUE,foreground="white",borderwidth=0)
        style.map("Primary.TButton",background=[("active","#1C6449"),("disabled","#B8CEC2")],foreground=[("disabled","white")])
        root.bind("<Escape>",lambda e:self.toggle_collapse())
        root.bind("<Command-Shift-H>" if sys.platform=="darwin" else "<Control-Shift-H>",lambda e:self.context_dialog())
        root.bind("<Command-Shift-D>" if sys.platform=="darwin" else "<Control-Shift-D>",lambda e:self.toggle_distribution())
        root.bind("<Command-Shift-S>" if sys.platform=="darwin" else "<Control-Shift-S>",lambda e:self.toggle_runtime())
        root.bind("<Next>",lambda e:self._keyboard_scroll(e,1))
        root.bind("<Prior>",lambda e:self._keyboard_scroll(e,-1))
        root.bind("<Command-r>" if sys.platform=="darwin" else "<Control-r>",lambda e:self.analyze())
        root.bind("<Command-q>" if sys.platform=="darwin" else "<Control-q>",lambda e:self.request_quit())
        root.protocol("WM_DELETE_WINDOW",self.request_quit)
        root.report_callback_exception=lambda *_: self._set_notice("界面操作异常；未输出聊天内容或凭据，请检查本机测试。")
        root.bind_all("<MouseWheel>",self._wheel)
        root.bind_all("<Button-4>",lambda e:self._wheel(e,-3))
        root.bind_all("<Button-5>",lambda e:self._wheel(e,3))

    def label(self,parent,text,*,color=INK,font=None,bg=None,wrap=True):
        w=tk.Label(parent,text=text,bg=bg or parent.cget("bg"),fg=color,font=font or self.font,anchor="w",justify="left",bd=0)
        if wrap:
            w.configure(wraplength=max(300,self.settings.width-80))
            w.bind("<Configure>",lambda e:w.configure(wraplength=max(100,e.width)))
        return w

    def button(self,parent,text,command,*,primary=False,enabled=True):
        b=ttk.Button(parent,text=text,command=command,width=0,style="Primary.TButton" if primary else "Jev.TButton")
        if not enabled:b.state(["disabled"])
        return b

    def _build(self):
        self.main=tk.Frame(self.root,bg=BG,highlightbackground=BORDER,highlightthickness=1)
        self.main.pack(fill="both",expand=True)
        head=tk.Frame(self.main,bg=PAPER,padx=12,pady=5);head.pack(fill="x")
        logo=self.label(head,"Jev",font=(self.family,14,"bold"),color=BLUE,wrap=False);logo.pack(side="left")
        names=tk.Frame(head,bg=PAPER);names.pack(side="left",padx=10)
        self.label(names,"对话判断",font=self.bold).pack(anchor="w")
        self.button(head,"×",self.request_quit).pack(side="right")
        self.button(head,"—",self.toggle_collapse).pack(side="right",padx=4)
        for widget in (head,logo,names,*names.winfo_children()):
            widget.bind("<ButtonPress-1>",self._drag_start);widget.bind("<B1-Motion>",self._drag)
            widget.bind("<ButtonRelease-1>",self._drag_end)
        self.mode_banner=self.label(self.main,"演示模式 · 全部为合成固定值，未连接飞书或模型" if self.demo else "本机面板 · 不向飞书发送分析 · 不自动发送回复",color=AMBER if self.demo else MUTED,bg=AMBER_BG if self.demo else BG,font=self.small)
        self.mode_banner.pack(fill="x",padx=16,pady=(6,4))
        controls=tk.Frame(self.main,bg=BG);controls.pack(fill="x",padx=16)
        values=[s.alias for s in self.cfg.sources if s.enabled]
        self.combo=ttk.Combobox(controls,textvariable=self.alias,state="readonly",values=["手动文本",*values],width=16)
        self.combo.pack(side="left",fill="x",expand=True)
        self.combo.bind("<<ComboboxSelected>>",self._source_changed)
        self.button(controls,"粘贴对话",self.text_dialog).pack(side="left",padx=(8,0))
        self.analyze_button=self.button(controls,"分析",self.analyze,primary=True)
        self.analyze_button.pack(side="left",padx=(8,0))
        tools=tk.Frame(self.main,bg=BG);tools.pack(fill="x",padx=16,pady=(3,2))
        tk.Checkbutton(tools,text="自动分析新消息",variable=self.auto,command=self._auto_changed,bg=BG,activebackground=BG,fg=MUTED,selectcolor=PAPER,font=self.small,bd=0,highlightthickness=0).pack(side="left")
        self.stop_button=self.button(tools,"停止",self.pause,enabled=False)
        self.stop_button.pack(side="right")
        self.button(tools,"连接 / 设置",self.settings_dialog).pack(side="right",padx=6)
        self.tab_buttons={}
        self.progress_line=self.label(self.main,"尚未开始",font=self.small,color=MUTED)
        self.progress_line.pack(fill="x",padx=18,pady=(2,4))
        self.source_line=self.label(self.main,"",font=self.small,color=MUTED)
        self.source_line.pack(fill="x",padx=18,pady=(0,5))
        foot=tk.Frame(self.main,bg=PAPER,padx=12,pady=5);foot.pack(side="bottom",fill="x")
        self.footer=self.label(foot,self.notice,font=self.small,color=MUTED);self.footer.pack(side="left",fill="x",expand=True)
        grip=tk.Label(foot,text="◢",fg=MUTED,bg=PAPER,cursor="sizing");grip.pack(side="right")
        grip.bind("<ButtonPress-1>",self._resize_start);grip.bind("<B1-Motion>",self._resize)
        self.reply_area=tk.Frame(self.main,bg=GREEN_BG,padx=12,pady=9,highlightbackground=BORDER,highlightthickness=1)
        self.reply_area.pack(side="bottom",fill="x")
        self.focus_area=tk.Frame(self.main,bg=PAPER,padx=12,pady=8)
        self.focus_area.pack(fill="x",padx=12,pady=(0,5))
        self.area=ScrollArea(self.main);self.area.pack(fill="both",expand=True,padx=(12,7))
        self.bubble=tk.Frame(self.root,bg=BLUE,padx=10,pady=8)
        bubble_text=tk.Label(self.bubble,text="Jev  +",bg=BLUE,fg="white",font=(self.family,15,"bold"),cursor="hand2")
        self.bubble_text=bubble_text;self._expanded_focus=None
        bubble_text.configure(takefocus=True,highlightthickness=1,highlightbackground=BLUE,highlightcolor=PAPER)
        bubble_text.pack(fill="both",expand=True)
        bubble_text.bind("<ButtonPress-1>",self._drag_start)
        bubble_text.bind("<B1-Motion>",self._drag)
        bubble_text.bind("<ButtonRelease-1>",self._bubble_release)
        bubble_text.bind("<Return>",lambda e:self.toggle_collapse())
        bubble_text.bind("<space>",lambda e:self.toggle_collapse())
        self._render()

    def _drag_start(self,event):
        self._dragging=True
        self._drag_origin=(event.x_root,event.y_root,self.root.winfo_x(),self.root.winfo_y())
    def _drag(self,event):
        if not hasattr(self,"_drag_origin"):return
        a,b,x,y=self._drag_origin
        self.root.geometry(f"+{max(0,x+event.x_root-a)}+{max(0,y+event.y_root-b)}")
        if self.follow_bounds:
            self.follow_offset=(max(0,x+event.x_root-a)-self.follow_bounds['x'],max(0,y+event.y_root-b)-self.follow_bounds['y'])
    def _drag_end(self,event=None):self._dragging=False
    def _bubble_release(self,event):
        self._drag_end()
        a,b,*_=getattr(self,"_drag_origin",(0,0,0,0))
        if abs(event.x_root-a)+abs(event.y_root-b)<5:self.toggle_collapse()
    def _resize_start(self,event):self._size_origin=(event.x_root,event.y_root,self.root.winfo_width(),self.root.winfo_height())
    def _resize(self,event):
        a,b,w,h=self._size_origin
        self.root.geometry(f"{max(440,min(720,w+event.x_root-a))}x{max(580,min(1200,h+event.y_root-b))}")
    def toggle_collapse(self):
        if self.filling:return
        if self.collapsed:
            self.bubble.pack_forget();self.main.pack(fill="both",expand=True)
            self.root.minsize(440,580);self.root.geometry(self._expanded_size)
            target=self._expanded_focus
            if target is not None and target.winfo_exists():target.focus_set()
            else:self.analyze_button.focus_set()
        else:
            self._expanded_focus=self.root.focus_get()
            self._expanded_size=f"{self.root.winfo_width()}x{self.root.winfo_height()}"
            self.main.pack_forget();self.bubble.pack(fill="both",expand=True)
            self.root.minsize(90,52);self.root.geometry("100x56")
            self.bubble_text.focus_set()
        self.collapsed=not self.collapsed
    def _wheel(self,event,forced=None):
        if self.closed or self.collapsed:return
        try:
            target=self.root.winfo_containing(event.x_root,event.y_root)
            if target is None or target.winfo_toplevel()!=self.root:return
            delta=forced if forced is not None else (-int(event.delta/120) if abs(event.delta)>=120 else -int(event.delta))
            if delta:self.area.scroll(delta)
        except tk.TclError:pass
    def select_tab(self,tab):
        # Compatibility for existing programmatic checks; there are no page tabs.
        self.show_runtime=tab=="运行状态";self._render();self.area.canvas.yview_moveto(0)
    def _card(self,fill=PAPER):
        c=Card(self.area.body,fill=fill);c.pack(fill="x",pady=(2,9));return c.body
    def _set_notice(self,text):
        self.notice=text;self.footer.configure(text=text)
    def _render_progress(self):
        if self.filling:
            text="等待填入结果" if self.fill_started else "请点击飞书中正确聊天的空输入框"
        elif self.busy:
            states=self.progress.states
            if states["capture"]=="running":text="正在读取消息…"
            elif states["ranking"]=="running":text="回复已写好，正在检查事实和承诺…"
            else:text="正在理解对话、拟写回复…"
        elif self.result_stale:text="本轮已停止，旧建议不能直接采用；请重新分析。"
        elif self.result is not None:
            text="示例结果，不是对你聊天的分析" if self.demo else "建议已更新 · "+time.strftime("%H:%M",time.localtime(self.result_at or time.time()))
            if self.result.errors:text="部分结果暂不可用，已标出受影响的内容。"
        else:text="还没有建议" if not self.last_error else "本轮未完成 · 可重试或检查连接"
        self.progress_line.configure(text=text)
        self.stop_button.configure(text="取消填入" if self.filling else "停止本轮" if self.busy else "停止检查")
        self.stop_button.state(["!disabled"] if (self.filling or self.busy or self.auto.get()) else ["disabled"])
        self._update_source_line()

    def _update_source_line(self):
        alias=self.result_alias or self.alias.get()
        if self.demo:text="演示会话 · 不会读取你的消息"
        elif alias=="手动文本":text="来源：你粘贴的对话 · 不读取飞书聊天"
        else:
            stamp=time.strftime("%H:%M:%S",time.localtime(self.read_at)) if self.read_at else "尚未读取"
            text=f"仅分析：{alias} · {stamp} · 不跟随飞书切换聊天"
        self.source_line.configure(text=text)

    def _freshness_tick(self):
        if not self.closed:
            self._update_source_line()
            self._schedule(10000,self._freshness_tick)

    def _toggle_follow(self):
        self.follow_offset=None;self.follow_bounds=None
        if self.follow_enabled.get():self.follower.start()
        else:self.follower.close()

    def _follow_tick(self):
        if self.closed:return
        if self.follow_enabled.get():
            self.follow_bounds=self.follower.poll()
            if self.follow_bounds and not self._dragging:
                x,y=anchor_position(self.follow_bounds,(self.root.winfo_width(),self.root.winfo_height()),
                                    (self.root.winfo_screenwidth(),self.root.winfo_screenheight()),self.follow_offset)
                if (x,y)!=(self.root.winfo_x(),self.root.winfo_y()):self.root.geometry(f"+{x}+{y}")
        self._schedule(250,self._follow_tick)

    def _render(self):
        if self.closed:return
        position=self.area.canvas.yview()[0] if self.area.canvas.yview() else 0
        self.area.clear();self._render_progress()
        self._render_focus()
        if self.result is None:self._render_empty()
        else:self._render_annotations()
        self._render_reply_dock()
        if self.show_runtime:self._render_runtime()
        self.root.update_idletasks()
        self.area.canvas.yview_moveto(position)

    def _render_focus(self):
        for child in self.focus_area.winfo_children():child.destroy()
        if self.result is None:
            self.focus_area.pack_forget();return
        if not self.focus_area.winfo_manager():
            self.focus_area.pack(fill="x",padx=12,pady=(0,5),before=self.area)
        row=tk.Frame(self.focus_area,bg=PAPER);row.pack(fill="x")
        self.label(row,"正在回复这句话",font=self.small,color=MUTED).pack(side="left")
        self.button(row,"收起对话" if self.show_context else "查看对话",self.context_dialog).pack(side="right")
        self.label(self.focus_area,focus_text(self.result),font=self.bold).pack(fill="x",pady=(3,0))

    def _render_annotations(self):
        body=self.area.body
        heading=tk.Frame(body,bg=BG);heading.pack(fill="x",pady=(2,6))
        self.label(heading,"Jev 判断 · 仅供参考",font=self.bold,color=BLUE).pack(side="left")
        self.button(heading,"收起分布" if self.show_distribution else "完整分布",self.toggle_distribution).pack(side="right")
        if self.result_stale:
            self.label(body,"本轮已停止，旧回复不可采用。",color=AMBER).pack(fill="x",pady=(0,6))
        if self.show_context:
            self.label(body,readable_context(self.result,self.result_alias),font=self.small).pack(fill="x",pady=(0,10))
        items=all_decisions(self.result)
        for d in items:
            if d.key=='best_reply' and self.edited_drafts:
                self.label(body,"以下排序与核查分值只对应模型原稿；你的修改稿未重新核查。",font=self.small,color=AMBER).pack(fill="x",pady=5)
            note=decision_annotation(d)
            row=tk.Frame(body,bg=TRACK,padx=10,pady=7);row.pack(fill="x",pady=(0,3))
            self.label(row,note['title'],font=self.small,color=MUTED).pack(fill="x")
            self.label(row,note['value'],font=self.bold).pack(fill="x",pady=(2,3))
            if self.show_distribution:
                self.label(row,note['distribution'],font=self.small).pack(fill="x")
                self.label(row,note['meta'],font=self.small,color=MUTED).pack(fill="x",pady=(3,0))
        if len(items)<8:
            self.label(body,f"已返回 {len(items)}/8 项；未返回的判断不显示虚构分值。",font=self.small,color=MUTED).pack(fill="x",pady=5)
        for stage,error in self.result.errors.items():
            self.label(body,f"{STAGE_LABELS.get(stage,stage)}：{friendly_error(error)}",color=AMBER,font=self.small).pack(fill="x",pady=4)
        self.label(body,"百分数是选项概率；置信度表示判断确定程度。Score 保持 0–4；Noul 没有单独的置信度。",font=self.small,color=MUTED).pack(fill="x",pady=6)
        controls=tk.Frame(body,bg=BG);controls.pack(fill="x",pady=5)
        self.button(controls,"收起原始字段" if self.show_raw else "原始字段",self.toggle_raw).pack(side="left")
        self.button(controls,"收起运行详情" if self.show_runtime else "运行详情",self.toggle_runtime).pack(side="left",padx=6)
        if self.demo:self.button(controls,"退出示例",self.exit_demo).pack(side="right")
        if self.show_raw:
            text=json.dumps({d.key:{'question':d.question,'answer':d.raw} for d in items},ensure_ascii=False,indent=2)
            self.label(body,text,font=self.small,color=MUTED).pack(fill="x",pady=5)
        self._evidence_footer()

    def toggle_raw(self):self.show_raw=not self.show_raw;self._render()
    def toggle_runtime(self):self.show_runtime=not self.show_runtime;self._render()
    def toggle_distribution(self):self.show_distribution=not self.show_distribution;self._render()
    def _keyboard_scroll(self,event,direction):
        if isinstance(event.widget,tk.Text):return
        self.area.canvas.yview_scroll(direction,"pages")
        return "break"

    def _render_reply_dock(self):
        if self.editor is not None and self.editor.winfo_exists():
            if getattr(self,'edit_epoch',-1)==self.epoch and getattr(self,'edit_revision',-1)==self.result_revision and not self.busy and not self.result_stale:
                return
        for child in self.reply_area.winfo_children():child.destroy()
        self.editor=None
        self.copy_buttons={};self.fill_buttons={};self.edit_buttons={}
        if self.result is None:
            self.reply_area.pack_forget();return
        if not self.reply_area.winfo_manager():self.reply_area.pack(side="bottom",fill="x",before=self.focus_area if self.focus_area.winfo_manager() else self.area)
        candidates=replies(self.result,self.cfg.minimum_grounding)
        if not candidates:
            self.label(self.reply_area,"正在拟写回复…" if self.busy else "尚无回复草稿。",font=self.small,color=MUTED).pack(fill="x");return
        best=self.result.ranking.get('best_reply',{}).get('choice')
        if best=='none' and not self.show_alternatives:
            self.label(self.reply_area,"这轮没有推荐回复",font=self.bold,color=AMBER).pack(fill="x")
            self.button(self.reply_area,"查看未采用草稿",self.toggle_alternatives).pack(anchor="w");return
        first=next((r for r in candidates if r.recommended),candidates[0])
        self._dock_reply(first)
        if len(candidates)>1:
            self.button(self.reply_area,"收起其他写法" if self.show_alternatives else "其他写法",self.toggle_alternatives).pack(anchor="w",pady=(4,0))
            if self.show_alternatives:
                # Extra drafts scroll with the annotations; the primary actions stay pinned.
                for r in candidates:
                    if r.key!=first.key:self._reply_card(r,r.index)

    def _dock_reply(self,r):
        p=self.reply_area
        edited=self.edited_drafts.get(r.key)
        self.label(p,"修改稿 · 未重新核查" if edited is not None else "建议这样回" if r.recommended else "回复草稿",font=self.small,color=BLUE).pack(fill="x")
        self.label(p,edited if edited is not None else r.text,font=self.font).pack(fill="x",pady=(4,6))
        if r.key in self.action_states:
            self.label(p,self.action_states[r.key],font=self.small,color=GREEN).pack(fill="x",pady=(0,4))
        row=tk.Frame(p,bg=GREEN_BG);row.pack(fill="x")
        active=not (self.busy or self.filling or self.result_stale)
        copy_ok=edited is not None or r.grounded is None or r.grounded>=self.cfg.minimum_grounding
        self.edit_buttons[r.key]=self.button(row,"改一下",lambda:self.edit_reply(r),enabled=active)
        self.edit_buttons[r.key].pack(side="left")
        fill_ok=edited is None and r.usable and self.bridge.available() and not self.demo and active and self.result_alias not in ('','手动文本')
        self.fill_buttons[r.key]=self.button(row,"填入飞书",lambda:self.fill_reply(r),enabled=fill_ok)
        self.fill_buttons[r.key].pack(side="left",padx=6)
        self.copy_buttons[r.key]=self.button(row,"复制回复",lambda:self.copy_display_reply(r),primary=True,enabled=copy_ok and active)
        self.copy_buttons[r.key].pack(side="right")
        if not fill_ok:
            reason="修改稿仅支持复制；不会沿用原分值填入。" if edited is not None else fill_explanation(r,demo=self.demo,busy=self.busy,stale=self.result_stale,manual=self.result_alias in ('','手动文本'),available=self.bridge.available())
            self.label(p,reason,font=self.small,color=MUTED).pack(fill="x",pady=(4,0))

    def _render_empty(self):
        p=self._card()
        if self.busy:
            self.label(p,"正在准备回复",font=(self.family,15,"bold")).pack(fill="x",pady=(0,8))
            self.label(p,"完成后会显示回复和需要你确认的信息。",color=MUTED).pack(fill="x")
            return
        self.label(p,"从一段对话开始",font=(self.family,15,"bold")).pack(fill="x",pady=(0,8))
        self.label(p,"粘贴消息 → 查看回复 → 按你的实际情况修改、复制",color=MUTED).pack(fill="x")
        self.button(p,"粘贴对话",self.text_dialog,primary=True).pack(anchor="w",pady=(14,4))
        self.button(p,"先看一个示例",self.load_demo).pack(anchor="w",pady=(3,0))
        if self.last_error:
            p=self._card(AMBER_BG)
            self.label(p,friendly_error(self.last_error),color=AMBER).pack(fill="x")
            self.button(p,"检查连接",self.settings_dialog).pack(anchor="w",pady=(8,0))
        missing=readiness(self.cfg)
        if missing:
            p=self._card()
            self.label(p,"使用前还差这些",font=self.bold).pack(fill="x",pady=(0,7))
            for item in missing:self.label(p,item,font=self.small,color=MUTED).pack(fill="x",pady=3)
            self.button(p,"查看连接设置",self.settings_dialog).pack(anchor="w",pady=(9,0))
        if not any(s.enabled for s in self.cfg.sources):
            p=self._card()
            self.label(p,"尚未连接飞书聊天",font=self.bold).pack(fill="x")
            self.label(p,"可以先用粘贴模式。接入飞书时，只添加你指定的聊天。",font=self.small,color=MUTED).pack(fill="x",pady=(6,0))


    def toggle_alternatives(self):
        self.show_alternatives=not self.show_alternatives
        self._render()


    def _reply_card(self,reply:Reply,order:int):
        color=BLUE_BG if reply.recommended else PAPER
        p=self._card(color)
        self.label(p,"建议这样回" if reply.recommended else "另一种写法" if reply.probability is not None else "草稿 · 正在检查",font=self.bold,color=BLUE).pack(fill="x")
        edited=self.edited_drafts.get(reply.key)
        self.label(p,edited if edited is not None else reply.text,font=(self.family,12)).pack(fill="x",pady=(8,9))
        badge,warning=draft_badge(reply)
        self.label(p,"修改稿 · 未重新核查" if edited is not None else badge,font=self.small,color=AMBER if warning or edited is not None else MUTED).pack(fill="x",pady=(0,6))
        state=self.action_states.get(reply.key,"")
        if state:self.label(p,state,font=self.small,color=GREEN if state.startswith("已复制") else MUTED).pack(fill="x",pady=(0,6))
        buttons=tk.Frame(p,bg=color);buttons.pack(fill="x")
        copy_ok=edited is not None or reply.grounded is None or reply.grounded>=self.cfg.minimum_grounding
        active=not (self.filling or self.busy or self.result_stale)
        self.copy_buttons[reply.key]=self.button(buttons,"复制回复",lambda r=reply:self.copy_display_reply(r),primary=True,enabled=copy_ok and active)
        self.copy_buttons[reply.key].pack(side="left")
        self.edit_buttons[reply.key]=self.button(buttons,"改一下",lambda r=reply:self.edit_reply(r),enabled=active)
        self.edit_buttons[reply.key].pack(side="left",padx=7)
        fill_ok=edited is None and reply.usable and self.bridge.available() and not self.demo and active and self.result_alias not in ("","手动文本")
        self.fill_buttons[reply.key]=self.button(buttons,"填入飞书",lambda r=reply:self.fill_reply(r),enabled=fill_ok)
        self.fill_buttons[reply.key].pack(side="left")
        if not fill_ok:
            reason="修改稿仅支持复制；不会沿用原分值填入。" if edited is not None else fill_explanation(reply,demo=self.demo,busy=self.busy,stale=self.result_stale,manual=self.result_alias in ("","手动文本"),available=self.bridge.available())
            self.label(p,reason,font=self.small,color=MUTED).pack(fill="x",pady=(7,0))

    def edit_reply(self,r:Reply):
        if self.busy or self.filling or not self._current_reply(r):return
        if self.editor and self.editor.winfo_exists():self.editor_text.focus_set();return
        epoch,revision=self.epoch,self.result_revision
        self.edit_epoch,self.edit_revision=epoch,revision
        for child in self.reply_area.winfo_children():child.destroy()
        win=tk.Frame(self.reply_area,bg=PAPER);win.pack(fill="x");self.editor=win
        p=win
        self.label(p,"修改回复",font=self.bold).pack(fill="x")
        self.label(p,"修改稿不沿用模型分值，不会自动填入或发送。",font=self.small,color=MUTED).pack(fill="x",pady=(3,5))
        box=tk.Text(p,bg=BG,fg=INK,wrap="word",undo=True,height=3,font=self.font,padx=8,pady=6,relief="flat")
        box.pack(fill="both",expand=True);box.insert("1.0",self.edited_drafts.get(r.key,r.text));box.focus_set()
        checked=tk.BooleanVar(value=False)
        tk.Checkbutton(p,text="我已核对事实、时间和承诺",variable=checked,bg=BG,activebackground=BG,selectcolor=PAPER,fg=INK).pack(anchor="w",pady=(8,0))
        note=self.label(p,"修改后请自己确认内容，再复制。",font=self.small,color=MUTED);note.pack(fill="x",pady=6)
        row=tk.Frame(p,bg=BG);row.pack(fill="x")
        def changed(*_):
            if box.edit_modified():checked.set(False);box.edit_modified(False)
        box.edit_modified(False);box.bind("<<Modified>>",changed)
        def cancel():
            win.destroy();self.editor=None;self._render()
        def copy():
            if epoch!=self.epoch or revision!=self.result_revision or not win.winfo_exists():
                self._set_notice("聊天或分析已变化，请重新查看回复。");return
            text=box.get("1.0","end-1c").strip()
            if not checked.get():note.configure(text="先核对修改稿，再勾选确认。",fg=AMBER);return
            if not 1<=len(text)<=500 or "\x00" in text:note.configure(text="请保留 1–500 字的回复。",fg=AMBER);return
            if epoch!=self.epoch or revision!=self.result_revision or not self._copy_edited(r,text):note.configure(text="聊天或分析已变化，请关闭后重新查看回复。",fg=AMBER);return
            cancel()
        def reset():box.delete("1.0","end");box.insert("1.0",r.text);checked.set(False)
        row.columnconfigure(1,weight=1)
        self.button(row,"恢复原文",reset).grid(row=0,column=0,sticky="w")
        self.button(row,"取消",cancel).grid(row=0,column=2,padx=8)
        self.button(row,"复制修改稿",copy,primary=True).grid(row=0,column=3,sticky="e")
        self.editor_text=box;self.editor_confirm=checked;self.editor_copy=copy

    def _copy_edited(self,r:Reply,text:str)->bool:
        if self.busy or self.filling or not self._current_reply(r):return False
        if not 1<=len(text)<=500 or "\x00" in text:return False
        try:
            self.root.clipboard_clear();self.root.clipboard_append(text);self.root.update_idletasks()
            if self.root.clipboard_get()!=text:return False
        except tk.TclError:return False
        self.action_states[r.key]="已复制修改稿（由你确认，未重新核查）"
        self.edited_drafts[r.key]=text
        self._set_notice("修改稿已复制。去飞书粘贴后检查，再自行发送。")
        self._render();return True



    def _render_runtime(self):
        p=self._card()
        self.label(p,"这轮做到了哪一步",font=(self.family,13,"bold")).pack(fill="x")
        self.label(p,"这里只记录读取和分析的进度，不代表已经替你处理了工作。",font=self.small,color=MUTED).pack(fill="x",pady=(3,7))
        for key,label in STAGE_LABELS.items():
            state=self.progress.states[key]
            row=tk.Frame(p,bg=PAPER);row.pack(fill="x",pady=7)
            self.label(row,label).pack(side="left")
            self.label(row,STATUS_LABELS[state],color=GREEN if state=="done" else RED if state=="failed" else MUTED,wrap=False).pack(side="right")
        self.label(p,self.progress.summary,font=self.bold).pack(fill="x",pady=(9,0))
        p=self._card()
        self.label(p,"本机运行与权限",font=self.bold).pack(fill="x",pady=(0,8))
        status=[("当前来源",self.alias.get()),("自动刷新",f"每 {self.settings.refresh_seconds} 秒，仅当前来源" if self.auto.get() else "已关闭"),
                ("窗口跟随",self.follower.status if self.follow_enabled.get() else "已关闭"),
                ("本次会话读取尝试",str(self.poll_count)),("本次分析尝试",str(self.attempt_count)),
                ("允许提交到模型服务","已允许" if self.cfg.allow_cloud else "未允许"),
                ("输入框填入","本机配置已启用，仍需辅助功能实测" if self.bridge.available() else "未启用 / 本机 helper 不可用"),
                ("自动发送","未提供此功能"),
                ("业务动作执行","仅建议；程序不会代你核对历史、做承诺或完成任务")]
        for label,value in status:self.label(p,f"{label}：{value}",font=self.small,color=MUTED).pack(fill="x",pady=4)
        if self.result:
            p=self._card();self.label(p,"本轮耗时",font=self.bold).pack(fill="x",pady=(0,7))
            for k,v in self.result.timings_ms.items():self.label(p,f"{STAGE_LABELS.get(k,k)}：{v} ms",font=self.small,color=MUTED).pack(fill="x",pady=2)
            if self.result.demo:self.label(p,"演示耗时没有代表性；不能当成真实服务延迟。",font=self.small,color=AMBER).pack(fill="x",pady=(8,0))
        if self.action_states:
            p=self._card();self.label(p,"候选操作记录（只在内存）",font=self.bold).pack(fill="x")
            for key,status in self.action_states.items():self.label(p,f"{key}：{status}",font=self.small,color=MUTED).pack(fill="x",pady=4)

    def _evidence_footer(self):
        chat=self.result.state["chat"]
        limits=chat.get("limitations",[])
        if limits:
            p=self._card()
            self.label(p,"还需要注意",font=self.bold).pack(fill="x")
            for limitation in limits:self.label(p,limitation,font=self.small,color=MUTED).pack(fill="x",pady=(4,0))
        else:
            self.label(self.area.body,f"依据最近 {len(chat['messages'])} 条消息，不代表全部历史。",font=self.small,color=MUTED).pack(fill="x",padx=5,pady=(4,8))

    def _dialog_text(self,title,text):
        win=tk.Toplevel(self.root);win.title(title);win.configure(bg=BG);win.attributes("-topmost",True)
        win.geometry("650x600");win.minsize(430,350)
        frame=tk.Frame(win,bg=BG,padx=14,pady=14);frame.pack(fill="both",expand=True)
        self.label(frame,title,font=(self.family,14,"bold")).pack(fill="x",pady=(0,10))
        box=tk.Text(frame,wrap="word",height=12,font=self.font,bg=PAPER,fg=INK,relief="flat",padx=12,pady=12)
        box.pack(fill="both",expand=True);box.insert("1.0",text);box.configure(state="disabled")
        self.button(frame,"关闭",win.destroy).pack(anchor="e",pady=(12,0))
        return win
    def decision_details(self,d):
        self._dialog_text(d.title+" · "+d.kind,json.dumps({"question_key":d.key,"question":d.question,"answer":d.raw},ensure_ascii=False,indent=2))
    def reply_details(self,r):
        d=next((d for d in all_decisions(self.result) if d.key==f"grounded_{r.index}"),None)
        text=f"候选 {r.index}\n{r.text}\n\n本轮相对偏好：{pct(r.probability)}\n事实约束 P(是)：{pct(r.grounded)}\n\n这些分数不表示任务完成度。\n"+(json.dumps(d.raw,ensure_ascii=False,indent=2) if d else "Jev 尚未返回核查结果。")
        self._dialog_text("候选详情",text)
    def context_dialog(self):
        if self.result:
            self.show_context=not self.show_context;self._render();self.area.canvas.yview_moveto(0)

    def text_dialog(self):
        if self.filling:return
        if self.text_editor and self.text_editor.winfo_exists():self.text_editor.lift();return
        win=tk.Toplevel(self.root);self.text_editor=win
        win.title("粘贴对话");win.configure(bg=BG);win.attributes("-topmost",True);win.geometry("570x460");win.minsize(440,400)
        frame=tk.Frame(win,bg=BG,padx=16,pady=16);frame.pack(fill="both",expand=True)
        self.label(frame,"复制几条消息，保留谁说的",font=self.bold).pack(fill="x")
        self.label(frame,"例如：我：进度还没确认。\n同事：明天下午能演示吗？",font=self.small,color=MUTED).pack(fill="x",pady=(4,7))
        box=tk.Text(frame,bg=PAPER,fg=INK,wrap="word",undo=True,height=8,font=self.font,relief="flat",padx=12,pady=12);box.pack(fill="both",expand=True)
        box.insert("1.0",self.text_input);box.focus_set()
        counter=self.label(frame,"",font=self.small,color=MUTED);counter.pack(fill="x",pady=(6,0))
        note=self.label(frame,"仅点击“生成回复”会把内容提交给已配置的模型。",font=self.small,color=MUTED);note.pack(fill="x",pady=(4,0))
        row=tk.Frame(frame,bg=BG);row.pack(fill="x",pady=(12,0))
        def changed(*_):
            n=len(box.get("1.0","end-1c"));counter.configure(text=f"{n} / {self.cfg.max_input_chars} 字",fg=AMBER if n>self.cfg.max_input_chars else MUTED)
            if box.edit_modified():box.edit_modified(False)
        box.bind("<<Modified>>",changed);changed()
        def paste():
            try:box.insert("insert",self.root.clipboard_get())
            except tk.TclError:note.configure(text="剪贴板里没有可粘贴的文字。",fg=AMBER)
        def accept():
            text=box.get("1.0","end-1c").strip()
            if not text:note.configure(text="先粘贴至少一条消息。",fg=AMBER);return
            if len(text)>self.cfg.max_input_chars:note.configure(text="内容过长，请只保留相关的几条消息。",fg=AMBER);return
            self.text_input=text;self.alias.set("手动文本");self._source_changed();win.destroy();self.analyze()
        self.button(row,"粘贴",paste).pack(side="left")
        self.button(row,"生成回复",accept,primary=True).pack(side="right")
        self.button(row,"取消",win.destroy).pack(side="right",padx=8)
        win.bind("<Command-Return>" if sys.platform=="darwin" else "<Control-Return>",lambda e:accept())

    def settings_dialog(self):
        if self.settings_window and self.settings_window.winfo_exists():self.settings_window.lift();return
        win=tk.Toplevel(self.root);self.settings_window=win
        win.title("连接与窗口设置");win.configure(bg=BG);win.attributes("-topmost",True);win.geometry("530x650")
        p=tk.Frame(win,bg=BG,padx=18,pady=18);p.pack(fill="both",expand=True)
        self.label(p,"连接情况",font=self.bold).pack(fill="x")
        if self.cfg.llm_backend == "codex_sdk":
            self.label(p,"回复生成：Codex SDK，使用本机 Codex 登录；不需要 LLM 密钥。",font=self.small,color=MUTED).pack(fill="x",pady=4)
        missing=readiness(self.cfg)
        for item in missing or ["模型配置已填写；真实连接以一次成功分析为准。"]:
            self.label(p,item,font=self.small,color=AMBER if missing else MUTED).pack(fill="x",pady=4)
        count=sum(s.enabled for s in self.cfg.sources)
        self.label(p,f"已配置 {count} 个聊天；这里只读取你选择的那一个。",font=self.small,color=MUTED).pack(fill="x",pady=5)
        tk.Checkbutton(p,text="跟随飞书窗口移动（本次）",variable=self.follow_enabled,command=self._toggle_follow,bg=BG,activebackground=BG,fg=INK,selectcolor=PAPER).pack(anchor="w",pady=4)
        self.label(p,"仅跟随窗口位置；分析来源仍需手动选择。",font=self.small,color=MUTED).pack(fill="x")
        self.button(p,"复制给 Codex 的连接检查任务",self.copy_setup_task).pack(anchor="w",pady=(6,14))
        self.label(p,"窗口不透明度",font=self.bold).pack(fill="x")
        tk.Scale(p,from_=.65,to=1,resolution=.01,orient="horizontal",variable=self.opacity,command=lambda value:self.root.attributes("-alpha",float(value)),bg=BG,highlightthickness=0).pack(fill="x",pady=(6,10))
        self.label(p,"拖标题移动；拖右下角缩放。Esc 收起，空格/回车恢复。\nCmd+R 分析；Cmd+Shift+H 对话；Cmd+Shift+D 分布。\nCmd+Shift+S 运行详情；PgUp/PgDn 滚动判断。",font=self.small,color=MUTED).pack(fill="x")
        note=self.label(p,"外观可以保存；此处不会更改账号或扩大读取范围。",font=self.small,color=MUTED);note.pack(fill="x",pady=(12,0))
        def save():
            if self.demo:note.configure(text="示例模式不保存设置。",fg=AMBER);return
            try:
                save_preferences(self.cfg.base,float(self.opacity.get()),self.root.winfo_width(),self.root.winfo_height())
                note.configure(text="外观已保存，下次启动时生效。",fg=GREEN)
            except (OSError,ValueError,CopilotError):note.configure(text="外观未保存，请检查项目目录是否可写。",fg=AMBER)
        row=tk.Frame(p,bg=BG);row.pack(fill="x",pady=(12,0))
        self.button(row,"保存外观",save,primary=True).pack(side="left")
        self.button(row,"关闭",win.destroy).pack(side="right")

    def copy_setup_task(self):
        task=("请检查当前 Jev 飞书悬浮助手的 config.toml、.env 与读取权限。"
              "复用已有飞书 CLI，不换绑、不扩大会话范围、不输出密钥。"
              "确认 Jev 判断接口和生成式 LLM 接口都可用；没有指定会话时先完成粘贴模式。"
              "模型凭据让我在本机隐藏输入。缺少授权或云端处理同意时先向我确认，不绕过。")
        try:
            self.root.clipboard_clear();self.root.clipboard_append(task);self.root.update_idletasks()
            self._set_notice("已复制检查任务，粘贴给本机 Codex 即可；不含你的密钥。")
        except tk.TclError:self._set_notice("复制失败，请重试。")

    def _invalidate(self,*,clear=True):
        self.epoch+=1;self.stop.set();self.result_revision+=1
        if clear:
            self.result=None;self.result_alias="";self.action_states={}
            self.edited_drafts={}
            self.result_stale=False;self.last_error="";self.show_alternatives=False
            self.show_context=False;self.show_raw=False
    def _source_changed(self,event=None):
        if self.filling:return
        self.auto.set(False);self._invalidate()
        self.progress=Progress();self.read_at=0;self._set_notice("已切换会话，点击“分析”读取这个聊天。")
        if self.demo:
            self.demo=False;self.mode_banner.configure(text="本机面板 · 不向飞书发送分析 · 不自动发送回复",fg=MUTED,bg=BG)
        self._render()
    def _auto_changed(self):
        if not self.auto.get():return
        if self.demo or self.alias.get()=="手动文本":
            self.auto.set(False);self._set_notice("先选择一个已授权的真实飞书会话。")
            return
        self._set_notice(f"自动模式只读取“{self.alias.get()}”；不会跟随你切换飞书聊天。")
        self.analyze(automatic=True)
    def _auto_tick(self):
        if self.closed:return
        if self.auto.get() and not self.busy and not self.filling:self.analyze(automatic=True)
        self._schedule(self.settings.refresh_seconds*1000,self._auto_tick)
    def pause(self):
        self.auto.set(False)
        if self.filling:
            self._cancel_fill()
            return
        self._invalidate(clear=False)
        if self.busy:self.result_stale=True
        for key,value in self.progress.states.items():
            if value in ("running","waiting"):self.progress.states[key]="cancelled"
        self._set_notice("已停止更新。点“分析”可重新开始；已发出的请求可能仍会计费。")
        self._render()
    def load_demo(self):
        if self.filling:return
        self.auto.set(False);self._invalidate()
        self.demo=True;self.alias.set("手动文本")
        self.result=demo_result(self.cfg.base);self.result_alias="合成工作对话";self.result_at=time.time()
        self.result_revision+=1;self.progress=Progress()
        for stage in self.progress.states:self.progress.update(stage,"done")
        self.mode_banner.configure(text="演示模式 · 全部为合成固定值，未连接飞书或模型",fg=AMBER,bg=AMBER_BG)
        self._set_notice("示例数据，可试用复制；不会写入飞书。")
        self._render()

    def exit_demo(self):
        self.demo=False;self.alias.set("手动文本");self._source_changed()
        self._set_notice("已退出示例，粘贴你的对话开始。")

    def analyze(self,automatic=False):
        if self.closed or self.filling:return
        if self.worker is not None and self.worker.is_alive():
            self._set_notice("上一轮请求仍在结束；不会并发读取新的会话。")
            return
        if self.demo and self.alias.get()=="手动文本" and not self.text_input:
            self.load_demo();return
        alias="" if self.alias.get()=="手动文本" else self.alias.get()
        if not alias and not self.text_input.strip():self.text_dialog();return
        try:
            self.cfg.check_models()
            if alias:self.cfg.source(alias)
        except CopilotError as exc:
            self.auto.set(False);self.last_error=str(exc)
            if self.result is not None:self.result_stale=True
            self._set_notice(friendly_error(str(exc)));self._render();return
        self.demo=False;self.mode_banner.configure(text="本机面板 · 不向飞书发送分析 · 不自动发送回复",fg=MUTED,bg=BG)
        self.stop=threading.Event();self.epoch+=1;epoch=self.epoch
        self.job_alias=alias or "手动文本";self.busy=True;self.worker_epoch=epoch;self.progress=Progress()
        self.edited_drafts={}
        self.last_error="";self.result_stale=bool(self.result);self.show_alternatives=False
        self.analyze_button.state(["disabled"])
        # Disable adoption while context is being checked; avoid showing an old draft as fresh.
        self._set_notice("正在读取选定上下文…")
        self.poll_count+=1;self._render()
        previous=self.signatures.get(alias,"")
        payload_text=self.text_input
        # Workers must never retain this UI or its Tcl-owned variables.
        engine,event_queue,cancelled=self.engine,self.events,self.stop
        gc.collect()  # Finalize unreachable, already-destroyed Tk objects on the main thread.
        def emit(stage,status,snapshot):event_queue.put((epoch,"progress",(stage,status,snapshot)))
        def work():
            try:
                outcome=engine.run(alias=alias,text=payload_text,automatic=automatic,
                    previous=previous,cancelled=cancelled,on_event=emit)
                event_queue.put((epoch,"outcome",outcome))
            except CopilotError as exc:event_queue.put((epoch,"error",str(exc)))
            except Exception:event_queue.put((epoch,"error","本机分析异常；未输出原始响应或凭据。"))
            finally:event_queue.put((epoch,"finished",None))
        self.worker=threading.Thread(target=work,name="overlay-analysis",daemon=True);self.worker.start()

    def _drain(self):
        if self.closed:return
        dirty=False
        for _ in range(80):
            try:epoch,kind,value=self.events.get_nowait()
            except queue.Empty:break
            if kind=="finished" and epoch==self.worker_epoch:
                self.busy=False;self.analyze_button.state(["!disabled"]);dirty=True
            if epoch!=self.epoch:continue
            if kind=="progress":
                stage,status,snapshot=value;self.progress.update(stage,status)
                if stage=="judgement" and status=="running":self.attempt_count+=1
                if snapshot is not None:
                    self.result=snapshot;self.result_alias=self.job_alias;self.result_stale=False
                    if stage=="judgement" and status=="running":self.action_states={};self.result_revision+=1
                dirty=True
            elif kind=="outcome":
                out:Outcome=value
                alias="" if out.alias=="手动文本" else out.alias
                self.read_at=out.read_at
                if out.result is not None:
                    self.result=out.result;self.result_alias=out.alias;self.result_at=time.time();self.result_revision+=1;self.result_stale=False
                    if not out.result.errors:self.signatures[alias]=out.signature
                    self._set_notice("挑一条回复，按实际情况改好再复制。" if not out.result.errors else "部分步骤没完成，请查看提示或重试。")
                else:
                    self.signatures[alias]=out.signature
                    if out.invalidates:self.result=None;self.result_alias="";self.action_states={};self.result_revision+=1
                    for k in ("judgement","candidates","ranking"):self.progress.update(k,"skipped")
                    self.result_stale=False
                    self._set_notice("没有新消息，保留现有建议。" if out.notice.startswith("没有新消息") else out.notice)
                dirty=True
            elif kind=="error":
                for stage,status in self.progress.states.items():
                    if status=="running":self.progress.update(stage,"failed")
                    elif status=="waiting":self.progress.update(stage,"skipped")
                self.auto.set(False);self.result=None;self.result_alias="";self.result_revision+=1
                self.last_error=value;self._set_notice(friendly_error(value));dirty=True
            elif kind=="fill_result":
                key,result=value;self.filling=False;self.fill_started=False;self.combo.configure(state="readonly")
                self.action_states[key]="已填入（读回确认），未发送" if result.verified else "填入被阻止" if result.status=="blocked" else "填入结果未确认"
                self._set_notice(result.message);dirty=True
        if dirty:self._render()
        self._schedule(80,self._drain)

    def _current_reply(self,r):
        return self.result is not None and not self.result_stale and any(x.key==r.key and x.text==r.text for x in replies(self.result,self.cfg.minimum_grounding))
    def copy_display_reply(self,r):
        if r.key in self.edited_drafts:self._copy_edited(r,self.edited_drafts[r.key])
        else:self.copy_reply(r)
    def copy_reply(self,r:Reply):
        if self.filling or self.busy or not self._current_reply(r) or (r.grounded is not None and r.grounded<self.cfg.minimum_grounding):return
        try:
            self.root.clipboard_clear();self.root.clipboard_append(r.text);self.root.update_idletasks()
            if self.root.clipboard_get()!=r.text:raise tk.TclError("clipboard readback mismatch")
            self.action_states[r.key]="已复制";self._set_notice("已复制，去飞书粘贴后检查再发送。")
        except tk.TclError:self.action_states[r.key]="复制未确认";self._set_notice("剪贴板写入未确认。")
        self._render()
    def fill_reply(self,r:Reply):
        if r.key in self.edited_drafts:
            self._set_notice("修改稿未重新核查，请使用复制。");return
        if self.demo or self.busy or self.filling or not r.usable or not self._current_reply(r) or not self.bridge.available():return
        if self.result_alias in ("","手动文本") or time.time()-self.result_at>120:
            self._set_notice("需要选定真实会话并重新分析；超过两分钟的草稿不能填入。")
            return
        text=f"将填入你手动选定的“{self.result_alias}”聊天输入框。\n\n请核对飞书当前聊天与上方来源一致。助手不能识别你实际打开的是哪个聊天。\n\n确认后 {self.settings.fill_delay_seconds} 秒内点击正确会话的空输入框。只写入，不按发送。"
        if not messagebox.askokcancel("确认填入目标",text,parent=self.root):return
        self.auto.set(False);self.filling=True;self.fill_started=False;self.fill_key=r.key;self.combo.configure(state="disabled")
        epoch=self.epoch;revision=self.result_revision
        self.action_states[r.key]="等待你选择空输入框";self._render()
        def countdown(n):
            if self.closed or not self.filling or epoch!=self.epoch or revision!=self.result_revision:return
            if n>0:
                self._set_notice(f"{n} 秒后尝试填入：请点击“{self.result_alias}”的空输入框；“取消填入”可取消。")
                self._countdown_id=self._schedule(1000,lambda:countdown(n-1));return
            self.fill_started=True;self._countdown_id=None
            self._set_notice("正在检查前台应用、输入框与空草稿；没有自动发送。")
            bridge,event_queue,text,key=self.bridge,self.events,r.text,r.key
            def work():
                result=bridge.fill(text,confirmed=True)
                event_queue.put((epoch,"fill_result",(key,result)))
            threading.Thread(target=work,name="overlay-fill",daemon=True).start()
        countdown(self.settings.fill_delay_seconds)
    def _cancel_fill(self):
        if self.fill_started:
            self._set_notice("写入操作已经开始，不能撤回；请等待读回结果，不要重复点击。")
            return
        if self._countdown_id:
            try:self.root.after_cancel(self._countdown_id)
            except tk.TclError:pass
        self._countdown_id=None
        self.filling=False;self.combo.configure(state="readonly")
        self.action_states[self.fill_key]="已取消（未填入）"
        self._set_notice("已取消倒计时，没有启动写入。")
        self._render()
    def request_quit(self):
        if self.filling:
            messagebox.showinfo("输入操作处理中","请等待本轮输入操作结果，或先点击“取消填入”停止倒计时。",parent=self.root);return
        self.close()
    def close(self):
        if self.closed:return
        self.closed=True;self.stop.set();self.auto.set(False)
        self.follower.close()
        for ident in self._after_ids:
            try:self.root.after_cancel(ident)
            except tk.TclError:pass
        root=self.root
        try:root.destroy()
        except tk.TclError:pass
        # Explicitly drop Tcl-owned references on the Tk thread, including dialogs.
        root.report_callback_exception=lambda *args: None
        for key,value in list(self.__dict__.items()):
            if isinstance(value,(tk.Misc,tk.Variable)):
                setattr(self,key,None)
        self.tab_buttons.clear();self.copy_buttons.clear();self.fill_buttons.clear();self.edit_buttons.clear()
        gc.collect()

def launch(config_path: str,*,demo=False) -> int:
    path=Path(config_path).expanduser().resolve()
    cfg=Config(base=path.parent) if demo else load_config(path)
    settings=OverlaySettings() if demo else load_preferences(path.parent,load_settings(path.parent/"overlay.toml"))
    def run():
        root=tk.Tk()
        app=OverlayApp(root,cfg,settings,demo=demo)
        root.mainloop()
        return 0
    if demo:return run()
    # Same directory lock as legacy bot: no duplicate local services or quota races.
    with instance_lock(cfg):return run()
