"""Fetch pinned source data and parse declarations; never import or execute a UFO."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "src/nsbi_agent/data/model_catalog.json"


def declarations(text: str, constructor: str, fields: tuple | None = None) -> list[dict]:
    rows = []
    for node in ast.parse(text).body:
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == constructor):
            row = {item.arg: ast.literal_eval(item.value)
                   for item in node.value.keywords if fields is None or item.arg in fields}
            row["source_line"] = node.lineno
            rows.append(row)
    return rows


def restriction_values(text: str) -> dict:
    values, block = {}, None
    for line in text.splitlines():
        fields = line.split("#", 1)[0].split()
        if not fields:
            continue
        if fields[0].lower() == "block":
            block = fields[1].upper()
        elif fields[0].lower() == "decay":
            values[("DECAY", (int(fields[1]),))] = float(fields[2])
            block = None
        elif block and len(fields) >= 2:
            code = tuple(int(item) for item in fields[:-1])
            values[(block, code)] = float(fields[-1].replace("D", "E"))
    return values


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="use cached source bytes only")
    parser.add_argument("--check", action="store_true", help="compare without rewriting the catalog")
    parser.add_argument("--from-local-ufo", action="store_true", help="read the verified owned UFO instead of cache/network")
    args = parser.parse_args()
    manifest = json.loads((ROOT / "scripts/model_sources.json").read_text())
    source, provenance = {}, {}
    cache = ROOT / ".cache/model_sources"
    if args.from_local_ufo:
        from nsbi_agent.model_store import inspect_local_model
        local = inspect_local_model(manifest["model"]["model_id"], manifest["model"]["version"])
        cache = Path(local["ufo_path"])
    for name, record in manifest["files"].items():
        url = (manifest["repository"].replace("github.com", "raw.githubusercontent.com")
               + "/" + manifest["commit"] + "/" + record["path"])
        path = cache / name
        if not path.is_file():
            if args.offline or args.from_local_ufo:
                raise FileNotFoundError("Missing cached source: " + str(path))
            with urllib.request.urlopen(url, timeout=30) as response:
                raw = response.read()
            if hashlib.sha256(raw).hexdigest() != record["sha256"]:
                raise ValueError("Downloaded source hash mismatch: " + name)
            cache.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != record["sha256"]:
            raise ValueError("Cached source hash mismatch: " + name)
        source[name] = raw.decode("utf-8")
        provenance[name] = {"url": url, "sha256": record["sha256"]}
    if source["version.info"].strip() != "tag: v" + manifest["model"]["version"]:
        raise ValueError("Model version does not match the manifest")

    restricted = restriction_values(source["restrict_massless_Hvlvl.dat"])
    parameters = {}
    for row in declarations(source["parameters.py"], "Parameter"):
        if row["nature"] != "external":
            continue
        key = (row["lhablock"].upper(), tuple(row["lhacode"]))
        parameters[row["name"]] = {
            "block": key[0], "code": row["lhacode"], "ufo_default": row["value"],
            "restriction_value": restricted.get(key), "source_line": row["source_line"],
        }
    particles = declarations(source["particles.py"], "Particle", ("name", "antiname"))
    catalog = {
        "catalog_version": 1, "model": manifest["model"],
        "provenance": {"repository": manifest["repository"], "commit": manifest["commit"],
                       "files": provenance},
        "parameters": parameters,
        "coupling_orders": {row["name"]: row["expansion_order"]
                            for row in declarations(source["coupling_orders.py"], "CouplingOrder")},
        "particle_names": sorted({row[key] for row in particles for key in ("name", "antiname")}),
        "scope": "Static declarations and restriction values; no evaluated MadGraph model.",
    }
    content = (json.dumps(catalog, indent=2, sort_keys=True) + "\n").encode()
    digest = hashlib.sha256(content).hexdigest()
    if args.check:
        if OUTPUT.read_bytes() != content or OUTPUT.with_suffix(".sha256").read_text().strip() != digest:
            raise ValueError("Catalog differs from the pinned source reconstruction")
    else:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_bytes(content)
        OUTPUT.with_suffix(".sha256").write_text(digest + "\n")
    print(json.dumps({"catalog": str(OUTPUT), "sha256": digest,
                      "external_parameters": len(parameters),
                      "particle_names": len(catalog["particle_names"]),
                      "check_only": args.check}))


if __name__ == "__main__":
    main()
