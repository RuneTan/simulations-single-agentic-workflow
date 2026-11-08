"""Interpret an actually imported restriction; no candidate search or selection."""
# MG5 2.9.27 import_ufo.RestrictModel: retain these as free external inputs,
# then reset their defaults after simplification (the zero marker is 1e-105).
RETAIN_DEFAULTS = {9.999999e-1: 1.0, 0.000001e-99: 0.0}


def parameter_constraints(values: dict, parameters: dict) -> dict:
    """External-parameter rules only; never evaluate UFO expressions/couplings."""
    groups = {}
    for name, value in values.items():
        block = parameters[name]['block'].upper()
        if value in (0, 1) or value in RETAIN_DEFAULTS or block == 'DECAY':
            continue
        groups.setdefault((block, abs(value)), []).append((name, 1 if value > 0 else -1))
    equal, signed = [], []
    for group in groups.values():
        if len(group) < 2:
            continue
        if len({sign for _, sign in group}) == 1:
            equal.append(sorted(name for name, _ in group))
        else:
            signed.append(dict(sorted(group)))
    return {
        'fixed_values': {n: v for n, v in values.items() if v in (0, 1)},
        'equality_groups': equal, 'signed_groups': signed,
        'retained_defaults': {n: RETAIN_DEFAULTS[v] for n, v in values.items() if v in RETAIN_DEFAULTS},
    }

