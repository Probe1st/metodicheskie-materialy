import sys
from pathlib import Path
repo_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_dir))

from app.catalog_store import create_catalog_store

catalog_path = repo_dir / "catalog.json"
if not catalog_path.is_file():
    create_catalog_store({"CATALOG_PROVIDER": "json", "CATALOG_PATH": catalog_path}).snapshot()
