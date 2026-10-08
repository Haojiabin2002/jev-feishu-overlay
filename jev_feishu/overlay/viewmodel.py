"""Pure presentation mapping, tested without a window or live model.

Choice probability != confidence != task completion.
Noul has NO separate confidence. Its true/false bar is not execution progress.
Score stays on its own rubric; the v1 work rubric is 0–4, not the Android 0–9.
"""
from __future__ import annotations
from dataclasses import dataclass
from ..models import AnalysisResult
from ..questions import ACTION_LABELS, INTENT_LABELS, judge_questions, ranking_questions

TITLES={"likely_request":"可能诉求", "communication_friction":"沟通状况",
        "next_action":"下一步建议", "facts_sufficient":"信息够不够回复", "best_reply":"候选回复排序"}
SCORE_LABELS={"0":"正常协作", "1":"轻微提醒", "2":"明确分歧", "3":"公开指责或升级", "4":"拒绝协作或冲突"}
ACTION_NOTE="只建议，未执行。复制或填入草稿不代表已完成业务动作。"

@dataclass(frozen=True)
class Decision:
    key: str
    title: str
    kind: str
    value: str
    probability: float | None
    confidence: float | None
    distribution: tuple[tuple[str,str,float], ...]
    note: str
    raw: dict
    question: dict

@dataclass(frozen=True)
class Reply:
    key: str
    index: int
    text: str
    probability: float | None
    grounded: float | None
    recommended: bool
    usable: bool
    caution: str

def pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.0%}"

def decision(key: str, a: dict, q: dict) -> Decision:
    kind=a["type"]
    title=TITLES.get(key, f"草稿 {key.split('_')[-1]}：事实和承诺有无依据" if key.startswith("grounded_") else key)
    if kind=="choice":
        labels=INTENT_LABELS if key=="likely_request" else ACTION_LABELS if key=="next_action" else {"none":"均不采用", **{f"reply_{i}":f"候选 {i}" for i in range(1,4)}}
        dist=tuple((k,labels.get(k,k),float(p)) for k,p in sorted(a["probabilities"].items(),key=lambda kv:kv[1],reverse=True))
        value=labels.get(a["choice"],a["choice"])
        note=ACTION_NOTE if key=="next_action" else "概率表示本轮更偏向哪一项；置信度单独显示，都不代表实际成功率。"
        return Decision(key,title,"Choice",value,a["probabilities"][a["choice"]],a["confidence"],dist,note,a,q)
    if kind=="score":
        maximum=len(q["criteria"])-1
        dist=tuple((k,SCORE_LABELS.get(k,k),float(p)) for k,p in sorted(a["probabilities"].items(),key=lambda kv:int(kv[0])))
        return Decision(key,title,"Score",f"{a['score']:.2f} / {maximum}",None,a["confidence"],dist,
                        "原始期望分，保持 0–4 标尺；不是“危险概率”，也不表示动作完成度。",a,q)
    n=a["noul"]
    value="倾向是" if n>=.7 else "倾向否" if n<=.3 else "不确定"
    return Decision(key,title,"Noul",value,n,None,(("true","是",n),("false","否",1-n)),
                    "这是对“是 / 否”的倾向，没有另一个置信度分数。",a,q)

def all_decisions(result: AnalysisResult) -> list[Decision]:
    rows=[]
    for key,q in judge_questions().items():
        if key in result.judgement:
            rows.append(decision(key,result.judgement[key],q))
    if len(result.candidates)==3:
        for key,q in ranking_questions(result.candidates).items():
            if key in result.ranking:
                rows.append(decision(key,result.ranking[key],q))
    return rows

def replies(result: AnalysisResult, threshold: float) -> list[Reply]:
    best=result.ranking.get("best_reply",{})
    rows=[]
    for i,text in enumerate(result.candidates,1):
        key=f"reply_{i}"
        probability=best.get("probabilities",{}).get(key)
        grounded=result.ranking.get(f"grounded_{i}",{}).get("noul")
        usable=bool(best) and grounded is not None and grounded >= threshold
        recommended=usable and best.get("choice")==key
        caution=""
        if not result.ranking: caution="排序未完成；可复制审阅，不可自动填入。"
        elif not usable: caution="未通过事实约束阈值；仅供查看，不可复制或填入。"
        elif best.get("choice")=="none": caution="Jev 建议均不采用；不会标注首选或允许填入。"
        rows.append(Reply(key,i,text,probability,grounded,recommended,
                          usable and best.get("choice")!="none",caution))
    return sorted(rows,key=lambda r: -(r.probability if r.probability is not None else -1))

STAGE_LABELS={"capture":"读取会话", "judgement":"Jev 判断", "candidates":"生成草稿", "ranking":"Jev 排序／核查"}
STATUS_LABELS={"waiting":"等待", "running":"执行中", "done":"已完成", "failed":"失败", "skipped":"未执行", "cancelled":"已停止"}

class Progress:
    def __init__(self):
        self.states={key:"waiting" for key in STAGE_LABELS}
    def update(self, stage: str, status: str):
        if stage not in self.states or status not in STATUS_LABELS:
            raise ValueError("unknown progress state")
        self.states[stage]=status
    @property
    def succeeded(self):
        return sum(v=="done" for v in self.states.values())
    @property
    def terminal(self):
        return sum(v in ("done","failed","skipped","cancelled") for v in self.states.values())
    @property
    def summary(self):
        return f"已成功 {self.succeeded}/4 步"  # Not a fabricated continuous percentage.
