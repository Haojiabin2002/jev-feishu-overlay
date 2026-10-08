from __future__ import annotations
from .config import Config
from .models import AnalysisResult
from .privacy import outbound_text
from .questions import INTENT_LABELS, ACTION_LABELS

def render(result: AnalysisResult, cfg: Config, alias: str = "手动文本") -> str:
    chat = result.state["chat"]
    rows = ["Jev 工作沟通助手" + ("【离线演示：固定响应，非模型实测】" if result.demo else ""),
            f"会话：{alias}｜本次上下文：{len(chat['messages'])} 条"]
    target = next((m for m in chat["messages"] if m["id"] == chat["focus_message_id"]), None)
    if target:
        rows.append(f"焦点原文 {target['id']} / {target['speaker']}：{target['text'][:180]}")
    for limitation in chat["limitations"]:
        rows.append("范围提示：" + limitation)
    rows.append("")
    judgement = result.judgement
    if judgement:
        intent = judgement["likely_request"]
        confidence = intent["confidence"]
        label = INTENT_LABELS.get(intent["choice"], "无法判断")
        rows.append("可能诉求：" + label + ("（判断分歧较大）" if confidence < 0.5 else ""))
        rows.append(f"输出集中度：{confidence:.0%}，不是正确率或对方真实心理概率。")
        friction = judgement["communication_friction"]
        rows.append(f"可见沟通摩擦：{friction['score']:.1f}/4（输出集中度 {friction['confidence']:.0%}）")
        rows.append("建议动作：" + ACTION_LABELS.get(judgement["next_action"]["choice"], "先核对上下文"))
        if judgement["facts_sufficient"]["noul"] < 0.7:
            rows.append("事实提示：信息可能不足；先澄清，不编造进度、承诺或截止时间。")
    else:
        rows.append("分析暂不可用：" + result.errors.get("judgement", "缺少结构化判断。"))
    rows += ["", "候选回复（仅供你审核；不会代你发送）"]
    if not result.candidates:
        rows.append("没有有效候选：" + result.errors.get("candidates", "生成失败。"))
    elif not result.ranking:
        rows.append("Jev 排序/事实约束检查未完成，以下是未验证草稿，无推荐顺序。")
        for i, candidate in enumerate(result.candidates, 1):
            rows.append(f"草稿 {i}：{candidate}")
    else:
        best = result.ranking["best_reply"]
        if best["choice"] == "none":
            rows.append("本轮建议：不直接采用这些候选；先确认信息或不追加回复。")
        else:
            grounded = result.ranking[f"grounded_{best['choice'].split('_')[-1]}"]["noul"]
            if grounded < cfg.minimum_grounding:
                rows.append("排序首选未通过事实约束阈值，本轮不推荐直接采用。")
        probs = best["probabilities"]
        count = 0
        for i in sorted(range(3), key=lambda i: probs[f"reply_{i+1}"], reverse=True):
            if result.ranking[f"grounded_{i+1}"]["noul"] < cfg.minimum_grounding:
                continue
            count += 1
            rows.append(f"候选 {count}｜本轮相对偏好 {probs[f'reply_{i+1}']:.0%}：{result.candidates[i]}")
        if not count:
            rows.append("三条候选均未通过事实约束阈值，已隐藏。")
        rows.append(f"“均不采用”的相对偏好：{probs['none']:.0%}。偏好分布包含全部候选，不是回复成功率。")
    rows += ["", f"模型处理耗时：{result.timings_ms.get('total', 0)} ms（不含轮询与飞书传输）",
             "这些是有限上下文下的辅助判断；不要据此断定他人真实意图。"]
    return outbound_text("\n".join(rows))
