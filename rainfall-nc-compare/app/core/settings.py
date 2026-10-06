from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "app/core/config.yaml"
CATEGORY_PATH = ROOT / "app/core/categories.yaml"


def load_config():
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


CATEGORIES = yaml.safe_load(CATEGORY_PATH.read_text(encoding="utf-8"))
NOTE = "Tanpa referensi observasi, lebih sensitif berarti lebih banyak mendeteksi, bukan lebih akurat."
