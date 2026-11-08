"""Shared checks with compact results and grouped Agent review items."""
import hashlib
import json
import math
from nsbi_agent.contracts import Check, ToolError, ToolResult


def fingerprint(value) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def number(value) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


class Report:
    def __init__(self, tool):
        self.tool, self.checks, self.errors = tool, [], []
        self.reviews, self.agent_fields = {}, []

    def check(self, name, passed, expected, actual, code='INCONSISTENT_INPUT'):
        self.checks.append(Check(name=name, status='pass' if passed else 'fail',
                                 expected=str(expected), actual=str(actual)))
        if not passed:
            self.errors.append(ToolError(code=code, message=f'{name}: {actual}'))
        return passed

    def unknown(self, name, actual, expected='Agent review'):
        topic = name.split('.')[0]
        group = self.reviews.setdefault(topic, {'topic': topic, 'count': 0, 'examples': []})
        group['count'] += 1
        if len(group['examples']) < 3:
            group['examples'].append({'item': name, 'value': str(actual)[:240], 'action': str(expected)[:240]})

    def fields(self, obj, known, path):
        self.agent_fields.extend(f'{path}.{name}' for name in sorted(set(obj) - set(known)))

    def result(self, data=None, artifacts=None):
        status = 'failed' if self.errors else 'needs_review' if self.reviews else 'succeeded'
        checks, groups = [], {}
        for item in self.checks:
            prefix = next((p for p in ('param_card.', 'model.source.', 'restriction.point.') if item.name.startswith(p)), None)
            if prefix and item.status == 'pass':
                groups[prefix] = groups.get(prefix, 0) + 1
            else:
                checks.append(item)
        checks.extend(Check(name=p.rstrip('.'), status='pass', expected='matching values', actual=f'{n} checks passed') for p, n in groups.items())
        summary = {'passed': sum(c.status == 'pass' for c in self.checks), 'differences': len(self.errors), 'review_topics': len(self.reviews)}
        payload = {'summary': summary, **(data or {}), 'review_items': list(self.reviews.values())}
        if self.agent_fields:
            payload['agent_fields'] = self.agent_fields
        return ToolResult(tool=self.tool, status=status, checks=checks, errors=self.errors,
                          data=payload, artifacts=artifacts or [])
