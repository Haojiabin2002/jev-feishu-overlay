"""Read-only macOS window geometry; no conversation identification or input writes."""
from pathlib import Path
import json
import math
import os
import queue
import subprocess
import sys
import threading


def valid_bounds(value):
    return (isinstance(value,dict)
            and all(type(value.get(k)) in (int,float) and math.isfinite(value[k]) for k in ('x','y','width','height'))
            and 250 < value['width'] < 20000 and 200 < value['height'] < 20000)


def anchor_position(bounds, panel, screen, offset=None):
    if not valid_bounds(bounds):raise ValueError('invalid window geometry')
    w,h=panel;sw,sh=screen
    dx,dy=offset if offset is not None else (bounds['width']-w-12,12)
    return (round(max(0,min(sw-w,bounds['x']+dx))),
            round(max(24,min(sh-h-8,bounds['y']+dy))))


class WindowFollower:
    def __init__(self, base: Path, settings):
        self.settings=settings
        p=Path(settings.window_helper)
        self.helper=p if p.is_absolute() else base/p
        self.process=None;self.thread=None;self.events=queue.Queue(maxsize=1)
        self.bounds=None;self.status='未跟随'

    def start(self):
        if self.process is not None:return
        if sys.platform!='darwin' or not self.helper.is_file():
            self.status='跟随不可用：窗口助手未编译';return
        env={k:v for k,v in os.environ.items() if k in ('HOME','PATH','LANG','TMPDIR')}
        try:
            self.process=subprocess.Popen([str(self.helper),self.settings.follow_bundle_id,'--watch'],
                stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                text=True,env=env,shell=False)
        except OSError:
            self.status='跟随不可用：窗口助手无法启动';return
        events=self.events;process=self.process
        def consume():
            for line in process.stdout:
                if len(line)>2048:continue
                try:state=json.loads(line)
                except ValueError:continue
                if not isinstance(state,dict):continue
                if events.full():
                    try:events.get_nowait()
                    except queue.Empty:pass
                try:events.put_nowait(state)
                except queue.Full:pass
        self.thread=threading.Thread(target=consume,name='jev-window-follow',daemon=True)
        self.thread.start()

    def poll(self):
        try:
            state=self.events.get_nowait()
            if state.get('status')=='window' and valid_bounds(state):
                self.bounds=state;self.status='正在跟随飞书窗口'
            else:
                self.bounds=None
                reasons={'accessibility':'本应用执行链路未获辅助功能权限',
                         'not_visible':'飞书未运行或已隐藏','no_window':'飞书窗口不可读或已最小化',
                         'no_chat_window':'没有可跟随的标准窗口'}
                self.status='未跟随：'+reasons.get(state.get('reason'),'窗口助手未返回有效坐标')
        except queue.Empty:pass
        if self.process is not None and self.process.poll() is not None:
            self.bounds=None;self.status='未跟随：窗口助手已退出'
        return self.bounds

    def close(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:self.process.wait(timeout=.6)
                except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=.6)
            if self.thread:self.thread.join(timeout=.3)
            if self.process.stdout:self.process.stdout.close()
            self.process=None
        self.bounds=None;self.status='未跟随'
