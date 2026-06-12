from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SubscriptionStatus(str, Enum):
    active = "active"        # Активна
    paused = "paused"        # На паузе
    completed = "completed"  # Завершена (конец сезона, терминальный)


class EpisodeStatus(str, Enum):
    found = "found"            # Найден у источника
    queued = "queued"          # Торрент отдан в Download Station
    downloaded = "downloaded"  # Задача DS завершилась (терминальный)
    error = "error"            # Выход только ручным повтором


class Episode(BaseModel):
    number: int
    title: str = ""
    status: EpisodeStatus = EpisodeStatus.found
    quality: str | None = None
    ds_task_id: str | None = None
    error: str | None = None
    progress: float = 0.0  # % закачки, имеет смысл только в статусе queued
    updated_at: str = Field(default_factory=now_iso)


class Subscription(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    source: str = "lostfilm"
    slug: str
    title: str
    season: int
    folder: str  # имя папки сезона, склеивается с settings.base_destination
    status: SubscriptionStatus = SubscriptionStatus.active
    episodes: list[Episode] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_iso)
    last_checked_at: str | None = None
    last_error: str | None = None

    def episode(self, number: int) -> Episode | None:
        return next((e for e in self.episodes if e.number == number), None)


class LostfilmSettings(BaseModel):
    cookies: str = ""  # строка Cookie из браузера после логина на lostfilm.tv
    mirrors: list[str] = Field(default_factory=lambda: ["https://www.lostfilm.tv"])


class Settings(BaseModel):
    base_destination: str = "video/Serials"  # путь относительно общих папок NAS
    # Метки качества так, как их пишет LostFilm: 1080, MP4 (это 720p), SD
    quality_priority: list[str] = Field(default_factory=lambda: ["1080", "MP4", "SD"])
    check_interval_hours: int = 12
    lostfilm: LostfilmSettings = Field(default_factory=LostfilmSettings)


class State(BaseModel):
    settings: Settings = Field(default_factory=Settings)
    subscriptions: list[Subscription] = Field(default_factory=list)

    def subscription(self, sub_id: str) -> Subscription | None:
        return next((s for s in self.subscriptions if s.id == sub_id), None)
