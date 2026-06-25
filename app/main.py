import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import config
from .engine import Engine
from .sources import LostfilmSource
from .storage import Store
from .synology import SynologyClient
from .web.routes import router, templates

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# httpx на INFO пишет каждый запрос с полным URL — это шум, и (хуже) светит
# в логах passwd и _sid из login-URL. Оставляем только его warning'и.
logging.getLogger("httpx").setLevel(logging.WARNING)


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
