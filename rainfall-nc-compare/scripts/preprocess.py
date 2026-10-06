"""Usage: python scripts/preprocess.py (from the project directory)."""
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.services.loader import DataStore

if __name__=="__main__":
    store=DataStore()
    store.prepare()
    print(store.metadata())
    store.close()
