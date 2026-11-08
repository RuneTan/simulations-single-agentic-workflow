"""Resolve SMEFTsim identity and source selection independently of tool execution."""
from nsbi_agent.physics_spec import ModelQuery

FLAVOURS = ('general', 'U35', 'MFV', 'top', 'topU3l')
EW_SCHEMES = ('alphaScheme', 'MwScheme')
RELEASE = '3.0.2'
OFFICIAL_COMMIT = 'db7d4a80bdcff424eee27dde71f1eb09ac894039'


def select_identity(request: ModelQuery) -> dict:
    from nsbi_agent.catalog import CatalogError
    identities = {f'SMEFTsim_{flavour}_{ew}_UFO': {
        'model_id': f'SMEFTsim_{flavour}_{ew}_UFO', 'version': RELEASE,
        'basis': 'Warsaw', 'flavour_scheme': flavour, 'ew_input_scheme': ew,
    } for flavour in FLAVOURS for ew in EW_SCHEMES}
    model_id = (f'SMEFTsim_{request.flavour_scheme or "U35"}_{request.ew_input_scheme or "MwScheme"}_UFO'
                if request.model_id is None else request.model_id)
    if model_id not in identities:
        raise CatalogError('UNSUPPORTED_MODEL', 'Choose flavour_scheme from ' + str(FLAVOURS)
                           + ' and ew_input_scheme from ' + str(EW_SCHEMES))
    identity = identities[model_id]
    for field in ('basis', 'flavour_scheme', 'ew_input_scheme'):
        value = getattr(request, field)
        if value is not None and value != identity[field]:
            raise CatalogError('MODEL_SELECTION_CONFLICT', f'{field}={value}; {model_id} requires {identity[field]}')
    if request.version is not None and request.version != RELEASE:
        raise CatalogError('UNSUPPORTED_MODEL_VERSION', 'Installed release: ' + RELEASE)
    return identity


def simulation_model_query(selection: dict) -> ModelQuery:
    """Keep tutorial omission defaults; explicit model premises select official sources.

    A resolved spec retains its collection, so replay never reinterprets defaults.
    Explicit tutorial + unsupported premises is an error, never a fallback.
    """
    fields = {k: v for k, v in selection.items()
              if k in {'model_id', 'version', 'basis', 'flavour_scheme', 'ew_input_scheme', 'collection'}}
    if 'collection' not in fields:
        explicit_identity = any(k in fields for k in ('model_id', 'basis', 'flavour_scheme', 'ew_input_scheme'))
        fields['collection'] = 'official' if explicit_identity else 'tutorial'
    return ModelQuery.model_validate(fields)
