"""Bounded, nonnegative tree-level EFT order semantics for MG5 2.9.27.

Compare allowed ordered pairs of amplitude orders, not selector spelling.
This does not establish that diagrams of those orders exist in a process.
"""
from itertools import product

REFERENCE_SELECTORS = {
    'sm': {'NP': (0, 'cap'), 'NPprop': (0, 'cap')},
    'interference': {'NP': (1, 'cap'), 'NP^2': (1, 'exact'), 'NPprop': (0, 'cap')},
    'dim6_squared': {'NP': (1, 'exact'), 'NP^2': (2, 'exact'), 'NPprop': (0, 'cap')},
    'propagator_interference': {'NP': (0, 'cap'), 'NPprop': (2, 'cap'), 'NPprop^2': (2, 'exact')},
}


def allowed_pairs(selectors, order):
    amplitude, squared = selectors.get(order), selectors.get(order + '^2')
    # Missing amplitude limits can invoke implicit model defaults. Do not guess.
    if amplitude is None and (squared is None or squared[0] != 0):
        return None
    limits = [rule[0] for rule in (amplitude, squared) if rule is not None]
    limit = min(limits)
    if limit > 16:
        return None  # Bounded coverage, rather than an unbounded enumeration.

    def accepts(value, rule):
        return rule is None or (value == rule[0] if rule[1] == 'exact' else value <= rule[0])

    return {(a, b) for a, b in product(range(limit + 1), repeat=2)
            if accepts(a, amplitude) and accepts(b, amplitude) and accepts(a + b, squared)}


def check_eft_orders(report, selectors, term):
    for order in ('NP', 'NPprop'):
        actual = allowed_pairs(selectors, order)
        expected = allowed_pairs(REFERENCE_SELECTORS[term], order)
        name = 'process_card.order_pairs.' + order
        if actual is None:
            report.unknown(name, 'implicit or unbounded order limits', 'explicit bounded amplitude/squared-order semantics')
        elif actual == expected:
            report.check(name, True, sorted(expected), sorted(actual))
        elif not actual.intersection(expected):
            report.check(name, False, sorted(expected), sorted(actual), 'CONTRIBUTION_ORDER_CONFLICT')
        else:
            # Partial/broader overlap may depend on model expansion limits or
            # absent diagrams. It is not a verified match or a proven zero.
            report.unknown(name, sorted(actual), 'requested pairs ' + str(sorted(expected)))
