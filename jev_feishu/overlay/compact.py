"""Single-page annotations retain the original decision values and semantics."""
from .viewmodel import pct


def decision_annotation(decision):
    value = decision.value
    if decision.kind == 'Choice':
        value += '  ' + pct(decision.probability)
    elif decision.kind == 'Score':
        label=max(decision.distribution,key=lambda item:item[2])[1]
        value=label+' · '+value
    elif decision.kind == 'Noul':
        value='是 '+pct(decision.probability)+' · 否 '+pct(1-decision.probability)
    if decision.key == 'next_action':
        value += ' · 未执行'
    meta = decision.kind + (' · 置信度不适用' if decision.confidence is None
                            else ' · 置信度 ' + pct(decision.confidence))
    return {'title': decision.title, 'value': value, 'meta': meta,
            'distribution': '  /  '.join(f'{label} {p:.0%}' for _, label, p in decision.distribution)}
