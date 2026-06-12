import asyncio
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from ..models import (EpisodeStatus, Subscription, SubscriptionStatus)

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

SUB_STATUS_RU = {
    SubscriptionStatus.active: "Активна",
    SubscriptionStatus.paused: "На паузе",
    SubscriptionStatus.completed: "Завершена",
}
EP_STATUS_RU = {
    EpisodeStatus.found: "Найден",
    EpisodeStatus.queued: "В очереди",
    EpisodeStatus.downloaded: "Скачан",
    EpisodeStatus.error: "Ошибка",
}
templates.env.globals.update(sub_status_ru=SUB_STATUS_RU, ep_status_ru=EP_STATUS_RU)


def _ctx(request: Request) -> dict:
    return {"store": request.app.state.store, "engine": request.app.state.engine,
            "sources": request.app.state.sources}


def _get_sub(request: Request, sub_id: str) -> Subscription:
    sub = request.app.state.store.state.subscription(sub_id)
    if sub is None:
        raise HTTPException(404, "Подписка не найдена")
    return sub


@router.get("/")
async def index(request: Request):
    state = request.app.state.store.state
    current = [s for s in state.subscriptions if s.status != SubscriptionStatus.completed]
    archive = [s for s in state.subscriptions if s.status == SubscriptionStatus.completed]
    return templates.TemplateResponse(request, "index.html", {
        "current": current, "archive": archive, "settings": state.settings,
    })


@router.post("/subscriptions")
async def add_subscription(request: Request, url: str = Form(...),
                           season: str = Form(""), folder: str = Form(""),
                           title: str = Form("")):
    c = _ctx(request)
    source = c["sources"]["lostfilm"]
    slug, url_season = source.parse_url(url.strip())
    # Ручной ввод переопределяет то, что достали из ссылки.
    season_num = int(season) if season.strip() else url_season
    if season_num is None:
        raise HTTPException(400, "Сезон не указан ни в ссылке, ни в поле «Сезон»")
    sub = Subscription(slug=slug, title=title.strip() or slug, season=season_num,
                       folder=folder.strip() or f"{slug}_S{season_num:02d}")
    async with c["store"].lock:
        c["store"].state.subscriptions.append(sub)
        await c["store"].save()
    # Бэкфил: сразу ищем и ставим в очередь все уже вышедшие серии сезона.
    asyncio.create_task(c["engine"].check_subscription(sub))
    return RedirectResponse(f"/subscriptions/{sub.id}", status_code=303)


@router.get("/subscriptions/{sub_id}")
async def subscription_page(request: Request, sub_id: str):
    sub = _get_sub(request, sub_id)
    return templates.TemplateResponse(request, "subscription.html", {"sub": sub})


@router.post("/subscriptions/{sub_id}/status")
async def set_status(request: Request, sub_id: str, action: str = Form(...)):
    sub = _get_sub(request, sub_id)
    store = request.app.state.store
    new_status = {"pause": SubscriptionStatus.paused,
                  "resume": SubscriptionStatus.active,
                  "complete": SubscriptionStatus.completed}.get(action)
    if new_status is None:
        raise HTTPException(400, f"Неизвестное действие: {action}")
    async with store.lock:
        sub.status = new_status
        await store.save()
    return RedirectResponse(f"/subscriptions/{sub_id}", status_code=303)


@router.post("/subscriptions/{sub_id}/delete")
async def delete_subscription(request: Request, sub_id: str):
    sub = _get_sub(request, sub_id)
    await request.app.state.engine.delete_subscription(sub)
    return RedirectResponse("/", status_code=303)


@router.post("/subscriptions/{sub_id}/check")
async def check_subscription(request: Request, sub_id: str):
    sub = _get_sub(request, sub_id)
    asyncio.create_task(request.app.state.engine.check_subscription(sub))
    return RedirectResponse(f"/subscriptions/{sub_id}", status_code=303)


@router.post("/subscriptions/{sub_id}/episodes/{number}/retry")
async def retry_episode(request: Request, sub_id: str, number: int):
    sub = _get_sub(request, sub_id)
    ep = sub.episode(number)
    if ep is None:
        raise HTTPException(404, "Эпизод не найден")
    asyncio.create_task(request.app.state.engine.retry_episode(sub, ep))
    return RedirectResponse(f"/subscriptions/{sub_id}", status_code=303)


@router.post("/check")
async def check_all(request: Request):
    asyncio.create_task(request.app.state.engine.check_all())
    return RedirectResponse("/", status_code=303)


@router.get("/settings")
async def settings_page(request: Request):
    state = request.app.state.store.state
    lostfilm_ok = await request.app.state.sources["lostfilm"].check_auth() \
        if state.settings.lostfilm.cookies else None
    return templates.TemplateResponse(request, "settings.html", {
        "settings": state.settings, "lostfilm_ok": lostfilm_ok,
    })


@router.post("/settings")
async def save_settings(request: Request,
                        base_destination: str = Form(...),
                        quality_priority: str = Form(...),
                        check_interval_hours: int = Form(...),
                        lostfilm_cookies: str = Form(""),
                        lostfilm_mirrors: str = Form(...)):
    store = request.app.state.store
    async with store.lock:
        s = store.state.settings
        from ..synology import normalize_destination
        s.base_destination = normalize_destination(base_destination)
        s.quality_priority = [q.strip() for q in quality_priority.split(",") if q.strip()]
        s.check_interval_hours = max(1, check_interval_hours)
        s.lostfilm.cookies = lostfilm_cookies.strip()
        s.lostfilm.mirrors = [m.strip().rstrip("/") for m in lostfilm_mirrors.splitlines()
                              if m.strip()]
        await store.save()
    return RedirectResponse("/settings", status_code=303)
