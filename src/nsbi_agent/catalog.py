"""Load checksum-verified static data owned by this package."""
import hashlib
import json
from pathlib import Path

from nsbi_agent.model_store import ModelStoreError, inspect_local_model
from nsbi_agent.ufo_catalog import EFT_BLOCKS, SM_BLOCKS, extract_catalog

CATALOG_PATH = Path(__file__).parent / "data/model_catalog.json"
DEFAULT_MODEL_ID = "SMEFTsim_U35_MwScheme_UFO"
DEFAULT_RESTRICTION = "massless_Hvlvl"


class CatalogError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def load_catalog(model_id: str = DEFAULT_MODEL_ID, version: str | None = None,
                 collection: str = 'tutorial') -> tuple[dict, str]:
    if collection == 'official':
        from nsbi_agent.model_selection import OFFICIAL_COMMIT, RELEASE
        try:
            local = inspect_local_model(model_id, version or RELEASE, registry_name='official_registry.json')
            if (local['provenance'].get('commit') != OFFICIAL_COMMIT
                    or local['provenance'].get('repository') != 'https://github.com/SMEFTsim/SMEFTsim'):
                raise CatalogError('MODEL_SOURCE_MISMATCH', 'Expected pinned official SMEFTsim sources')
            root = Path(local['ufo_path'])
            provenance = {**local['provenance'], 'manifest_sha256': local['manifest_sha256'],
                          'collection': collection}
            content, digest = extract_catalog(
                (root/'parameters.py').read_text(), (root/'particles.py').read_text(),
                (root/'coupling_orders.py').read_text(), json.dumps(local['model'], sort_keys=True),
                json.dumps(provenance, sort_keys=True))
            return json.loads(content), digest
        except (ModelStoreError, OSError, ValueError, SyntaxError, KeyError) as error:
            raise CatalogError(getattr(error, 'code', 'CATALOG_UNAVAILABLE'), str(error)) from error
    if collection != 'tutorial':
        raise CatalogError('UNSUPPORTED_COLLECTION', collection)
    try:
        raw = CATALOG_PATH.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        expected = CATALOG_PATH.with_suffix(".sha256").read_text().strip()
        if digest != expected:
            raise CatalogError("CATALOG_HASH_MISMATCH", "Model catalog checksum does not match.")
        catalog = json.loads(raw)
    except (OSError, json.JSONDecodeError) as error:
        raise CatalogError("CATALOG_UNAVAILABLE", str(error)) from error
    model = catalog["model"]
    if model_id != model["model_id"]:
        raise CatalogError("UNSUPPORTED_MODEL", "Supported model: " + model["model_id"])
    if version is not None and version != model["version"]:
        raise CatalogError("UNSUPPORTED_MODEL_VERSION", "Supported version: " + model["version"])
    try:
        inspect_local_model(model_id, model["version"], catalog["provenance"]["files"])
    except ModelStoreError as error:
        raise CatalogError(error.code, str(error)) from error
    return catalog, digest


def reference_sm_inputs(catalog: dict) -> dict[str, float]:
    """Keep the existing massless-light-fermion/CKM assumptions, independently of WC restrictions."""
    values = {}
    for name, row in catalog["parameters"].items():
        if row["block"] in SM_BLOCKS:
            value = row["restriction_value"] if row["block"] in {"MASS", "YUKAWA", "CKMBLOCK"} else row["ufo_default"]
            values[name] = float(row["ufo_default"] if value is None else value)
    return values


def restriction_status(parameter: dict) -> str:
    value = parameter["restriction_value"]
    if value in (0, 1):
        return "fixed_at_import"
    if value is None:
        return "not_listed_in_restriction"
    return "nonzero_import_value_not_a_run_point"


def coefficient_names(catalog: dict) -> list[str]:
    return [name for name, row in catalog["parameters"].items()
            if row["block"] in EFT_BLOCKS]
