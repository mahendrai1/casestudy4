"""Load integration/threshold config (edited as code, per deck slide 6)."""
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "integrations.yaml"


def load_config(path=CONFIG_PATH):
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    total = sum(i["weight"] for i in cfg["integrations"].values())
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"Integration weights must sum to 1.0, got {total}")
    cfg["by_source_name"] = {v["source_name"]: k for k, v in cfg["integrations"].items()}
    return cfg
