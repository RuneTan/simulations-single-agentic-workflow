"""Export independent inputs and coefficients of all installed official UFOs."""
import argparse
import json
from pathlib import Path

from nsbi_agent.catalog import load_catalog
from nsbi_agent.model_selection import EW_SCHEMES, FLAVOURS, RELEASE


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    models = {}
    for flavour in FLAVOURS:
        for ew in EW_SCHEMES:
            name = f'SMEFTsim_{flavour}_{ew}_UFO'
            catalog, digest = load_catalog(name, RELEASE, 'official')
            models[name] = {'catalog_sha256': digest, 'parameters': catalog['parameters']}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(models, indent=2)+'\n')
    print(args.output)
