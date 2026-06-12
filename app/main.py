import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import config
from .engine import Engine
from .sources import LostfilmSource
from .storage import Store
from .synology import SynologyClient
from .web.routes import router

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = Store(config.STATE_PATH)
    synology = SynologyClient(config.SYNO_URL, config.SYNO_USERNAME, config.SYNO_PASSWORD)
    lostfilm = LostfilmSource(lambda: store.state.settings.lostfilm)
    engine = Engine(store, synology, {"lostfilm": lostfilm})

    app.state.store = store
    app.state.engine = engine
    app.state.sources = {"lostfilm": lostfilm}

    engine.start()
    yield
    await engine.stop()
    await lostfilm.close()
    await synology.close()


app = FastAPI(title="Synology Serials Downloader", lifespan=lifespan)
app.include_router(router)
