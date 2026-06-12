from dataclasses import dataclass
from typing import Protocol


class SourceError(Exception):
    """Любая ошибка источника; текст показывается пользователю в UI."""


class AuthError(SourceError):
    """Авторизация на источнике невалидна (cookie протух) — баннер в UI."""


@dataclass
class FoundEpisode:
    number: int
    title: str = ""


class Source(Protocol):
    name: str

    def extract_slug(self, url: str) -> str:
        """Достаёт slug сериала из любой вставленной пользователем ссылки."""
        ...

    async def list_episodes(self, slug: str, season: int) -> list[FoundEpisode]:
        """Вышедшие (доступные для скачивания) эпизоды сезона."""
        ...

    async def fetch_torrent(self, slug: str, season: int, number: int,
                            quality_priority: list[str]) -> tuple[bytes, str, str]:
        """Возвращает (содержимое .torrent, имя файла, выбранное качество)."""
        ...

    async def check_auth(self) -> bool:
        """True, если авторизация источника сейчас валидна."""
        ...
