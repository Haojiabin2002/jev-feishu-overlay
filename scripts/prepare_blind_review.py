#!/usr/bin/env python3
"""Prepare neutral review materials OUTSIDE this code project.

Does not start an agent and does not claim to isolate OS access. The local Codex
orchestrator must create a fresh, non-inheriting review thread separately.
"""
from pathlib import Path
import argparse
import json
import shlex
import shutil
import sys
import plistlib
import subprocess

ROOT=Path(__file__).resolve().parent.parent
TASK='''# 办公任务体验

你是一名独立评审 agent，从普通办公人员视角体验这个聊天回复助手，不是代码审查员。
本次只给你办公任务、运行入口和界面。不读取项目源码、AGENTS、设计说明、改版记录、测试结论或主代理对话。
不要声称自己是真人，不要把合成演示当成真实模型效果。

先启动 办公助手体验.app（或 launch.command）。这个入口是离线示例，不会连接飞书、调用模型或发送消息。
附图为原生控件的合成离屏预览，不是实际点击记录。
能够操作 GUI 时必须实际操作；只有截图时只能给出静态观察，不得声称完成了点击或复制。
无法启动时记录具体阻塞，不能通过阅读源码推断“体验成功”。

## 任务

1. 先从界面判断这个工具能帮你做什么、还需要准备什么，记录你的第一反应。
2. 根据示例对话，弄清这次要回复哪条消息、是谁发来的，找到一条可采用的回复。
3. 把回复改成你愿意发给同事的说法，复制并核对剪贴板。不要实际发送。
4. 想弄清“为什么这样建议”，找到相应信息。说明其中哪些数字或句子帮助你作决定，哪些没有帮助。
5. 判断复制、填入和发送有什么区别，尝试了解不能填入时应怎么办。
6. 查看此前的消息，再回到回复。收起窗口并恢复。说明哪里有额外操作负担。
7. 查看来源选择、暂停/恢复和运行状态，说明哪些行为在示例中可验证、哪些还需要真实飞书或模型连接。不要自行申请账号、读取配置或凭据，也不要接入其他会话。
8. 找到一条以前的分析记录，核对它对应的原消息、建议和执行状态，再回到当前消息。说明记录与当前建议是否容易区分、能否再次找到这条记录。

## 输出 REVIEW_RESULT.md

四部分：是否可用；是否好用；无效或看不懂的文案；其他改进。
每个问题写清：你操作了什么 / 实际看到什么 / 对任务的影响 / 建议 / 严重程度。
附可复现的步骤和截图；任务逐项标为完成、失败、未测试，不编造耗时、正确率、满意度或真人反馈。
区分真实 GUI 操作、截图观察、推测和未验证的模型效果。
不要为发现问题而强行挑错；不要预设结论。
'''

def prepare(destination: Path)->Path:
    destination=destination.expanduser().resolve()
    if destination==ROOT or destination.is_relative_to(ROOT):
        raise ValueError('Review kit must be outside the project so project instructions are not inherited.')
    destination.mkdir(parents=True,exist_ok=False)
    (destination/'REVIEW_TASK.md').write_text(TASK,encoding='utf-8')
    # Copy only user-facing screenshots, not the audit conclusions.
    previews=destination/'screens';previews.mkdir()
    for source,target in [('offscreen-demo.png','reply.png'),('offscreen-edit.png','edit.png'),('offscreen-details.png','details.png')]:
        shutil.copyfile(ROOT/'assets'/source,previews/target)
    python=ROOT/'.venv/bin/python'
    if not python.is_file():python=Path(sys.executable).resolve()
    launcher='#!/bin/bash\nset -euo pipefail\ncd '+shlex.quote(str(ROOT))+'\nexec '+shlex.quote(str(python))+' -m jev_feishu overlay --demo\n'
    (destination/'launch.command').write_text(launcher,encoding='utf-8');(destination/'launch.command').chmod(0o700)
    app=destination/'办公助手体验.app/Contents';(app/'MacOS').mkdir(parents=True);(app/'Resources').mkdir()
    bootstrap=app/'Resources/launcher.zsh';bootstrap.write_text(launcher,encoding='utf-8');bootstrap.chmod(0o700)
    binary=app/'MacOS/launch'
    subprocess.run(['xcrun','clang','-std=c11','-Os',str(ROOT/'vendor/jev_jarvis/launcher.c'),'-o',str(binary)],check=True,capture_output=True)
    binary.chmod(0o700)
    (app/'Info.plist').write_bytes(plistlib.dumps({'CFBundleName':'办公助手体验','CFBundleIdentifier':'local.jev.office.review.native','CFBundleExecutable':'launch','CFBundlePackageType':'APPL','CFBundleVersion':'0.3.1','LSUIElement':True,'NSHighResolutionCapable':True}))
    shutil.copyfile(ROOT/'vendor/jev_jarvis/LICENSE',app/'Resources/LICENSE.txt')
    (destination/'REVIEW_STATUS.json').write_text(json.dumps({'status':'not_started','independent_agent_started':False,'materials':'neutral tasks and screenshots only'},indent=2),encoding='utf-8')
    return destination

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT.parent/'jev-native-review-kit')
    args=parser.parse_args()
    try: print(prepare(args.output))
    except (ValueError,FileExistsError) as error:raise SystemExit(str(error))
    print('Prepared only; a fresh independent reviewer has NOT been started.')
if __name__=='__main__':main()
