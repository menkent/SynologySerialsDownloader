import asyncio
import logging

from .models import Episode, EpisodeStatus, Subscription, SubscriptionStatus, now_iso
from .sources.base import Source
from .storage import Store
from .synology import (DuplicateTaskError, SynologyClient, SynologyError,
                       normalize_destination)

log = logging.getLogger(__name__)

DS_POLL_SECONDS = 300
# Задача DS считается докачанной и в finished, и в seeding (файлы уже на месте).
_DS_DONE = {"finished", "seeding"}


class Engine:
    """Два фоновых цикла: проверка источников и опрос Download Station."""

    def __init__(self, store: Store, synology: SynologyClient, sources: dict[str, Source]):
        self.store = store
        self.synology = synology
        self.sources = sources
        self._tasks: list[asyncio.Task] = []

    def start(self) -> None:
        self._tasks = [
            asyncio.create_task(self._check_loop(), name="check-sources"),
            asyncio.create_task(self._poll_loop(), name="poll-ds"),
        ]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()

    # --- циклы ------------------------------------------------------------

    async def _check_loop(self) -> None:
        while True:
            try:
                await self.check_all()
            except Exception:
                log.exception("Сбой цикла проверки источников")
            hours = max(1, self.store.state.settings.check_interval_hours)
            await asyncio.sleep(hours * 3600)

    async def _poll_loop(self) -> None:
        while True:
            try:
                await self.poll_downloads()
            except Exception:
                log.exception("Сбой опроса Download Station")
            await asyncio.sleep(DS_POLL_SECONDS)

    # --- проверка источников -----------------------------------------------

    async def check_all(self) -> None:
        for sub in list(self.store.state.subscriptions):
            if sub.status == SubscriptionStatus.active:
                await self.check_subscription(sub)

    async def check_subscription(self, sub: Subscription) -> None:
        source = self.sources[sub.source]
        try:
            found = await source.list_episodes(sub.slug, sub.season)
        except Exception as e:
            async with self.store.lock:
                sub.last_error = str(e)
                sub.last_checked_at = now_iso()
                await self.store.save()
            return

        async with self.store.lock:
            known = {e.number for e in sub.episodes}
            for f in sorted(found, key=lambda f: f.number):
                if f.number not in known:
                    sub.episodes.append(Episode(number=f.number, title=f.title))
            sub.last_checked_at = now_iso()
            sub.last_error = None
            await self.store.save()

        for ep in [e for e in sub.episodes if e.status == EpisodeStatus.found]:
            await self.queue_episode(sub, ep)

    async def queue_episode(self, sub: Subscription, ep: Episode) -> None:
        """Найден → В очереди: забрать торрент у источника, отдать в DS."""
        source = self.sources[sub.source]
        settings = self.store.state.settings
        # Нормализация спасает и старые настройки, где путь сохранён с /volume1.
        destination = normalize_destination(
            f"{settings.base_destination}/{sub.folder.strip('/')}")
        try:
            torrent, filename, quality = await source.fetch_torrent(
                sub.slug, sub.season, ep.number, settings.quality_priority)
            await self.synology.ensure_folder(destination)
            task_id = await self.synology.create_task(torrent, filename, destination)
        except DuplicateTaskError:
            # Торрент уже в Download Station (добавлен раньше или вручную) —
            # считаем серию скачанной (терминальный статус). Своего task_id DS
            # в этом случае не отдаёт, отслеживать прогресс всё равно нечем.
            async with self.store.lock:
                ep.status = EpisodeStatus.downloaded
                ep.quality = quality
                ep.progress = 100.0
                ep.error = None
                ep.updated_at = now_iso()
                await self.store.save()
            return
        except Exception as e:
            async with self.store.lock:
                ep.status = EpisodeStatus.error
                ep.error = str(e)
                ep.updated_at = now_iso()
                await self.store.save()
            return
        async with self.store.lock:
            ep.status = EpisodeStatus.queued
            ep.ds_task_id = task_id
            ep.quality = quality
            ep.error = None
            ep.progress = 0.0
            ep.updated_at = now_iso()
            await self.store.save()

    async def retry_episode(self, sub: Subscription, ep: Episode) -> None:
        """Ручной повтор из статуса Ошибка."""
        await self.queue_episode(sub, ep)

    # --- опрос Download Station ---------------------------------------------

    async def poll_downloads(self) -> None:
        queued = [(sub, ep)
                  for sub in self.store.state.subscriptions
                  for ep in sub.episodes
                  if ep.status == EpisodeStatus.queued and ep.ds_task_id]
        if not queued:
            return
        try:
            tasks = await self.synology.get_tasks([ep.ds_task_id for _, ep in queued])
        except SynologyError as e:
            log.warning("Опрос DS не удался: %s", e)
            return

        async with self.store.lock:
            for sub, ep in queued:
                task = tasks.get(ep.ds_task_id)
                if task is None:
                    ep.status = EpisodeStatus.error
                    ep.error = "Задача исчезла из Download Station"
                elif task.get("status") in _DS_DONE:
                    ep.status = EpisodeStatus.downloaded
                    ep.progress = 100.0
                    ep.error = None
                elif task.get("status") == "error":
                    ep.status = EpisodeStatus.error
                    ep.error = f"Download Station: {task.get('status_extra') or 'ошибка задачи'}"
                else:
                    size = int(task.get("size") or 0)
                    done = int(((task.get("additional") or {}).get("transfer") or {})
                               .get("size_downloaded") or 0)
                    ep.progress = round(done * 100 / size, 1) if size else 0.0
                ep.updated_at = now_iso()
            await self.store.save()

    # --- операции над подписками ---------------------------------------------

    async def delete_subscription(self, sub: Subscription) -> None:
        """Снять незавершённые задачи в DS, файлы не трогать (см. глоссарий)."""
        pending = [ep.ds_task_id for ep in sub.episodes
                   if ep.status == EpisodeStatus.queued and ep.ds_task_id]
        try:
            await self.synology.delete_tasks(pending)
        except SynologyError as e:
            log.warning("Не удалось снять задачи DS при удалении подписки: %s", e)
        async with self.store.lock:
            self.store.state.subscriptions.remove(sub)
            await self.store.save()
