"""Extract verified UFO declarations with AST literals; never import model code."""
import ast
from functools import lru_cache
import hashlib
import json
import math

SM_BLOCKS = frozenset({'SMINPUTS', 'MASS', 'YUKAWA', 'CKMBLOCK', 'DECAY'})
EFT_BLOCKS = frozenset({'SMEFT', 'SMEFTCPV', 'SMEFTFV'})


def parameter_unit(name: str, row: dict) -> str:
    if name == 'Gf':
        return 'GeV^-2'
    if row['block'] in {'MASS', 'YUKAWA', 'DECAY', 'SMEFTCUTOFF'} or name == 'MW':
        return 'GeV'
    return 'dimensionless'


def declarations(text: str, constructor: str, fields=None) -> list[dict]:
    rows = []
    for node in ast.parse(text).body:
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name) and node.value.func.id == constructor):
            row = {item.arg: ast.literal_eval(item.value) for item in node.value.keywords
                   if fields is None or item.arg in fields}
            row['source_line'] = node.lineno
            rows.append(row)
    return rows


@lru_cache(maxsize=12)
def extract_catalog(parameters_text: str, particles_text: str, orders_text: str, identity_json: str,
                    provenance_json: str) -> tuple[str, str]:
    """Cache immutable JSON by verified source bytes, not by mutable model paths."""
    parameters, coordinates = {}, set()
    for row in declarations(parameters_text, 'Parameter'):
        if row['nature'] != 'external':
            continue
        block, code = row['lhablock'].upper(), row['lhacode']
        value = row['value']
        if (row['type'] != 'real' or type(value) not in (int, float) or not math.isfinite(value)
                or block not in SM_BLOCKS | EFT_BLOCKS | {'SMEFTCUTOFF', 'SWITCHES'}):
            raise ValueError('Unsupported external declaration: ' + row['name'])
        key = block, tuple(code)
        if row['name'] in parameters or key in coordinates:
            raise ValueError('Duplicate parameter name or LHA coordinate: ' + row['name'])
        coordinates.add(key)
        parameters[row['name']] = {'block': block, 'code': code, 'ufo_default': value,
                                  'restriction_value': None, 'source_line': row['source_line']}
    particles = declarations(particles_text, 'Particle', {'name', 'antiname'})
    identity = json.loads(identity_json)
    catalog = {'catalog_version': 2, 'model': {**identity, 'variant': 'official SMEFTsim release'},
               'provenance': json.loads(provenance_json), 'parameters': parameters,
               'coupling_orders': {p['name']: p['expansion_order'] for p in declarations(orders_text, 'CouplingOrder')},
               'particle_names': sorted({p[k] for p in particles for k in ('name', 'antiname')}),
               'sm_input_defaults': 'selected-ufo-defaults@1',
               'scope': 'Static external declarations; no evaluated UFO expressions or generator import.'}
    content = json.dumps(catalog, sort_keys=True, indent=2) + '\n'
    return content, hashlib.sha256(content.encode()).hexdigest()
