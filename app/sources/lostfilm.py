"""Источник LostFilm.

Схема работы (подсмотрена у существующих интеграций, селекторы могут
дрейфовать вместе с вёрсткой сайта — при поломке чинить здесь):

1. Страница сезонов /series/<slug>/seasons доступна без логина. Кнопки
   вышедших серий имеют onclick="PlayEpisode('SSSNNNEEE')", где код —
   конкатенация id сериала, номера сезона (3 цифры) и серии (3 цифры).
2. /v_search.php?a=<код> (требует cookie залогиненного пользователя)
   отдаёт страничку-редирект на торрент-каталог (retre/tracktor).
3. Страница каталога содержит блоки по качествам (1080 / MP4 / SD)
   со ссылками на .torrent — берём первое качество из приоритета.
"""

import logging
import re
from typing import Callable
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

from ..models import LostfilmSettings
from .base import AuthError, FoundEpisode, SourceError

_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_PLAY_RE = re.compile(r"PlayEpisode\('(\d+)'\)")


class LostfilmSource:
    name = "lostfilm"

    def __init__(self, settings_getter: Callable[[], LostfilmSettings]):
        self._settings = settings_getter
        # Короткий connect-timeout: заблокированный домен должен отваливаться
        # быстро, чтобы успеть перебрать остальные зеркала.
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(30, connect=10), follow_redirects=True)

    async def close(self) -> None:
        await self._client.aclose()

    def parse_url(self, url: str) -> tuple[str, int | None]:
        m = re.search(r"/series/([^/?#]+)(?:/season_(\d+))?", url)
        if not m:
            raise SourceError(
                "Не похоже на ссылку LostFilm: ожидаю …/series/<имя-сериала>/…")
        season = int(m.group(2)) if m.group(2) else None
        return m.group(1), season

    def series_url(self, slug: str, season: int | None = None) -> str:
        """Публичная ссылка на страницу сериала/сезона — для перехода из UI.

        Базой берём первое настроенное зеркало (оно же доступно пользователю)."""
        mirrors = self._settings().mirrors or ["https://www.lostfilm.tv"]
        base = mirrors[0].rstrip("/")
        path = f"/series/{slug}/season_{season}" if season else f"/series/{slug}"
        return base + path

    # --- HTTP с перебором зеркал ---------------------------------------

    def _headers(self) -> dict:
        h = {"User-Agent": _UA}
        cookies = self._settings().cookies.strip()
        if cookies:
            try:
                cookies.encode("ascii")
            except UnicodeEncodeError:
                raise SourceError(
                    "Cookie LostFilm содержит не-ASCII символы (например «…») — "
                    "похоже, вставлен не настоящий Cookie, а заготовка")
            h["Cookie"] = cookies
        return h

    async def _get(self, path: str) -> httpx.Response:
        errors: list[str] = []
        for mirror in self._settings().mirrors:
            try:
                r = await self._client.get(mirror.rstrip("/") + path,
                                           headers=self._headers())
                if r.status_code == 200:
                    return r
                errors.append(f"{mirror}: HTTP {r.status_code} (итоговый URL {r.url})")
            except httpx.HTTPError as e:
                # str(ReadTimeout) часто пустой — тип исключения обязателен.
                errors.append(f"{mirror}: {type(e).__name__}: {e}".rstrip(": "))
            log.warning("LostFilm %s: %s", path, errors[-1])
        raise SourceError(f"Все зеркала LostFilm не отдали {path}: " + "; ".join(errors))

    async def _fetch_external(self, url: str, what: str) -> httpx.Response:
        """Запрос по ссылке из цепочки v_search→каталог→.torrent.

        Ссылка может вести как на зеркало (туда нужны куки), так и на
        сторонний торрент-домен (туда куки не отправляем).
        """
        mirror_hosts = {httpx.URL(m).host for m in self._settings().mirrors}
        headers = self._headers() if httpx.URL(url).host in mirror_hosts \
            else {"User-Agent": _UA}
        try:
            r = await self._client.get(url, headers=headers)
        except httpx.HTTPError as e:
            raise SourceError(
                f"Не удалось получить {what} ({url}): {type(e).__name__}: {e}".rstrip(": "))
        if r.status_code != 200:
            raise SourceError(f"{what}: HTTP {r.status_code} ({r.url})")
        return r

    # --- Source protocol ------------------------------------------------

    async def list_episodes(self, slug: str, season: int) -> list[FoundEpisode]:
        codes = await self._episode_codes(slug, season)
        return [FoundEpisode(number=ep) for s, ep, _code in codes if s == season]

    async def fetch_torrent(self, slug: str, season: int, number: int,
                            quality_priority: list[str]) -> tuple[bytes, str, str]:
        codes = await self._episode_codes(slug, season)
        code = next((c for s, ep, c in codes if s == season and ep == number), None)
        if code is None:
            raise SourceError(f"Серия S{season:02d}E{number:02d} не найдена на странице сериала")

        redirect_page = await self._get(f"/v_search.php?a={code}")
        if "/login" in str(redirect_page.url) or "Вам необходимо авторизоваться" in redirect_page.text:
            raise AuthError("Cookie LostFilm протух — обновите его в настройках")
        # Ссылки в цепочке бывают относительными — резолвим от страницы-источника.
        catalog_url = urljoin(str(redirect_page.url),
                              self._extract_catalog_url(redirect_page.text))

        catalog = await self._fetch_external(catalog_url, "торрент-каталог")
        torrent_url, quality = self._pick_torrent(catalog.text, quality_priority)
        torrent_url = urljoin(str(catalog.url), torrent_url)

        torrent = await self._fetch_external(torrent_url, f".torrent ({quality})")
        if not torrent.content.startswith(b"d"):  # bencode всегда начинается с 'd'
            raise SourceError("Скачанный файл не похож на .torrent")
        filename = f"{slug}.S{season:02d}E{number:02d}.{quality}.torrent"
        return torrent.content, filename, quality

    async def check_auth(self) -> bool:
        if not self._settings().cookies.strip():
            return False
        try:
            r = await self._get("/my/")
        except SourceError:
            return False
        # Неавторизованным /my/ отдаёт крошечную страницу-заглушку
        # с meta-refresh на главную (URL при этом не меняется).
        return "/login" not in str(r.url) and 'http-equiv="refresh"' not in r.text[:1500]

    # --- парсинг ----------------------------------------------------------

    async def _episode_codes(self, slug: str, season: int) -> list[tuple[int, int, str]]:
        """[(сезон, серия, код PlayEpisode), …] для вышедших серий сезона.

        Берём постраничный URL /season_<N>, а не общий /seasons: у идущих
        сейчас сериалов «Гид по сериям» (/seasons) приходит пустым (только
        заглушка «0 сезон»), а кнопки PlayEpisode живут на странице сезона.
        В коде PlayEpisode('SSSNNNEEE') средние 3 цифры — сезон, последние 3 —
        серия, так что сезон определяется однозначно.
        """
        path = f"/series/{slug}/season_{season}"
        page = await self._get(path)
        soup = BeautifulSoup(page.text, "html.parser")
        result = []
        for el in soup.select('[onclick*="PlayEpisode("]'):
            m = _PLAY_RE.search(el.get("onclick", ""))
            if not m:
                continue
            code = m.group(1)
            if len(code) < 7:
                continue
            s, ep = int(code[-6:-3]), int(code[-3:])
            # ep == 999 — псевдосерия «торрент всего сезона», а не настоящий эпизод.
            if ep == 999:
                continue
            result.append((s, ep, code))
        if not result:
            raise SourceError(
                f"На странице {path} не нашлось ни одной серии — "
                "проверьте slug/номер сезона или вёрстка сайта изменилась")
        return result

    @staticmethod
    def _extract_catalog_url(html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        # Страница v_search — это мини-редиректор: единственная ссылка/мета-refresh.
        a = soup.find("a", href=re.compile(r"^(https?://|/)"))
        if a:
            return a["href"]
        meta = soup.find("meta", attrs={"http-equiv": re.compile("refresh", re.I)})
        if meta and "url=" in meta.get("content", "").lower():
            return re.split(r"url=", meta["content"], flags=re.I)[1].strip()
        raise AuthError("LostFilm не отдал ссылку на торрент-каталог — "
                        "вероятно, cookie протух")

    @staticmethod
    def _pick_torrent(html: str, quality_priority: list[str]) -> tuple[str, str]:
        soup = BeautifulSoup(html, "html.parser")
        items: list[tuple[str, str]] = []  # (текст блока, ссылка на .torrent)
        for a in soup.find_all("a", href=re.compile(r"\.torrent|td\.php|download")):
            block_text = a.find_parent().get_text(" ", strip=True) if a.find_parent() else ""
            items.append((block_text, a["href"]))
        if not items:
            raise SourceError("В торрент-каталоге не нашлось ссылок на .torrent")
        for quality in quality_priority:
            for text, href in items:
                if quality.lower() in text.lower():
                    return href, quality
        # Ничего не совпало с приоритетом — берём первую ссылку, качество неизвестно.
        return items[0][1], "unknown"
