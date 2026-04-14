"""Load .osop workflow files (YAML or JSON)."""
import yaml
import json
from pathlib import Path


def load_workflow(path: str) -> dict:
    """Load and parse an .osop file. Returns raw dict."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Workflow file not found: {path}")
    with open(p, encoding="utf-8") as f:
        if p.suffix in (".yaml", ".yml", ".osop"):
            data = yaml.safe_load(f)
        elif p.suffix == ".json":
            data = json.load(f)
        else:
            raise ValueError(f"Unsupported file format: {p.suffix}. Use .yaml, .osop, or .json")
    if not isinstance(data, dict):
        raise ValueError(f"Workflow file must be a YAML/JSON object, got: {type(data)}")
    return data
