"""Synthetic, offline demonstration. Fixed answers are never used as a live fallback."""
from __future__ import annotations
from .questions import judge_questions, ranking_questions

TRANSCRIPT = """我：报价校验原型还在联调，完成时间没确认。
同事：客户回传报价后，异常项准备怎么提醒？
我：先标出异常并提示人工复核，不自动修改客户填写的数据。
同事：明天下午能给我一个可演示版本吗？"""

REPLIES = ["我先确认联调进度，再回复你可演示的时间。",
           "演示需要覆盖哪些流程？我据此确认明天下午是否能准备好。",
           "目前完成时间还没确认；要不要先看已经能跑通的部分？"]

def choice_answer(question: dict, winner: str, confidence: float = 0.75) -> dict:
    keys = list(question["criteria"])
    remaining = 0.2/(len(keys)-1)
    return {"type": "choice", "choice": winner, "confidence": confidence,
            "probabilities": {key: 0.8 if key == winner else remaining for key in keys}}

class DemoClient:
    def judge(self, state):
        q = judge_questions()
        return {
            "likely_request": choice_answer(q["likely_request"], "action"),
            "next_action": choice_answer(q["next_action"], "clarify"),
            "communication_friction": {"type": "score", "score": 0.1, "confidence": 0.7,
                "probabilities": {"0": 0.9, "1": 0.1, "2": 0.0, "3": 0.0, "4": 0.0},
                "legend": dict(enumerate(q["communication_friction"]["criteria"]))},
            "facts_sufficient": {"type": "noul", "noul": 0.2},
        }, {}

    def generate(self, state):
        return list(REPLIES), {}

    def rank(self, state, candidates):
        return {
            "best_reply": {"type": "choice", "choice": "reply_2", "confidence": 0.48,
                           "probabilities": {"reply_1": 0.22, "reply_2": 0.60, "reply_3": 0.12, "none": 0.06}},
            **{f"grounded_{i+1}": {"type": "noul", "noul": 0.92} for i in range(3)},
        }, {}
