"""Resolve explicit run-card factorization switches as RunCardLO.check_validity does.

Presence matters: per-beam flags override the shared flag. Unknown implicit
beam/default state remains None, never a successful fixed-scale assertion.
"""


def effective_factorization_flags(actual):
    shared = actual.get('fixed_fac_scale')
    first, second = actual.get('fixed_fac_scale1'), actual.get('fixed_fac_scale2')
    if first is not None and second is not None:
        return first, second
    if first is not None or second is not None:
        if first is None:
            first = shared if shared is not None else False if actual.get('lpp1') == 0 else None
        if second is None:
            second = shared if shared is not None else False if actual.get('lpp2') == 0 else None
        return first, second
    if shared is None:
        return (False, False) if actual.get('lpp1') == actual.get('lpp2') == 0 else (None, None)
    if shared is True:
        return True, True
    if actual.get('lpp1') in (2, 3, 4):
        return True, shared
    if actual.get('lpp1') is None:
        return None, None
    if actual.get('lpp2') in (2, 3, 4):
        return shared, True
    if actual.get('lpp2') is None:
        return None, None
    return shared, shared
