import logging
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import config
from .engine import Engine
from .sources import LostfilmSource
from .storage import Store
from .synology import SynologyClient
from .web.routes import router, templates

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
logging.basicConfig(level=logging.INFO, format=_LOG_FORMAT)  # stdout -> docker logs
# httpx на INFO пишет каждый запрос с полным URL — это шум, и (хуже) светит
# в логах passwd и _sid из login-URL. Оставляем только его warning'и.
logging.getLogger("httpx").setLevel(logging.WARNING)

# Дублируем логи в файл на volume (лежит рядом со state.json, переживает
# пересоздание контейнера, читается без docker logs). Хендлер на корневом
# логгере — ловит app.*, но не uvicorn-овский HTTP-шум (у него propagate=False).
# Ротация, чтобы файл не рос без предела: 3 файла по 2 МБ.
try:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    _file = RotatingFileHandler(config.DATA_DIR / "app.log",
                                maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    _file.setFormatter(logging.Formatter(_LOG_FORMAT))
    logging.getLogger().addHandler(_file)
except OSError as _e:
    logging.getLogger(__name__).warning("Не удалось открыть файл лога на %s: %s",
                                         config.DATA_DIR, _e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = Store(config.STATE_PATH)
    synology = SynologyClient(config.SYNO_URL, config.SYNO_USERNAME, config.SYNO_PASSWORD)
    lostfilm = LostfilmSource(lambda: store.state.settings.lostfilm)
    engine = Engine(store, synology, {"lostfilm": lostfilm})

    app.state.store = store
    app.state.engine = engine
    app.state.sources = {"lostfilm": lostfilm}

    sources = app.state.sources
    templates.env.globals["source_url"] = \
        lambda sub: sources[sub.source].series_url(sub.slug, sub.season)

    engine.start()
    yield
    await engine.stop()
    await lostfilm.close()
    await synology.close()


app = FastAPI(title="Synology Serials Downloader", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "web" / "static"), name="static")
app.include_router(router)
