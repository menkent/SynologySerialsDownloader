import os
from pathlib import Path

# Все пути/креды задаются переменными окружения контейнера, не хранятся в state.json.
DATA_DIR = Path(os.environ.get("APP_DATA_DIR", "/data"))
STATE_PATH = DATA_DIR / "state.json"

SYNO_URL = os.environ.get("SYNO_URL", "").rstrip("/")  # например http://192.168.1.10:5000
SYNO_USERNAME = os.environ.get("SYNO_USERNAME", "")
SYNO_PASSWORD = os.environ.get("SYNO_PASSWORD", "")
