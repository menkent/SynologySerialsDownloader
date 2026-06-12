#!/usr/bin/env python3
"""Локальная диагностика LostFilm: авторизация и вся цепочка до .torrent.

Использование:
    LOSTFILM_COOKIES='lf_session=…; …' python3 scripts/lostfilm_probe.py [slug] [season] [episode]

Куку можно не класть в env, а записать одной строкой в файл .cookie
в корне репозитория (он в .gitignore).
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models import LostfilmSettings  # noqa: E402
from app.sources.lostfilm import LostfilmSource  # noqa: E402

MIRRORS = ["https://www.lostfilm.download", "https://www.lostfilm.tv"]


def read_cookies() -> str:
    if os.environ.get("LOSTFILM_COOKIES"):
        return os.environ["LOSTFILM_COOKIES"].strip()
    cookie_file = Path(__file__).resolve().parent.parent / ".cookie"
    if cookie_file.exists():
        return cookie_file.read_text(encoding="utf-8").strip()
    return ""


async def main() -> None:
    slug = sys.argv[1] if len(sys.argv) > 1 else "Margos_Got_Money_Troubles"
    season = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    episode = int(sys.argv[3]) if len(sys.argv) > 3 else 1

    cookies = read_cookies()
    print(f"Кука: {'есть, ' + str(len(cookies)) + ' символов' if cookies else 'НЕТ (env LOSTFILM_COOKIES / файл .cookie)'}")

    settings = LostfilmSettings(cookies=cookies, mirrors=MIRRORS)
    src = LostfilmSource(lambda: settings)
    try:
        print(f"1) check_auth: {'OK, авторизован' if await src.check_auth() else 'НЕ авторизован'}")

        eps = await src.list_episodes(slug, season)
        print(f"2) {slug}, сезон {season}: серии {[e.number for e in eps]}")

        codes = await src._episode_codes(slug)
        code = next((c for s, e, c in codes if s == season and e == episode), None)
        if code is None:
            print(f"3) серия E{episode:02d} не найдена — дальше идти некуда")
            return
        print(f"3) код PlayEpisode для E{episode:02d}: {code}")

        page = await src._get(f"/v_search.php?a={code}")
        print(f"4) v_search: итоговый URL {page.url}, {len(page.text)} байт")
        if "/login" in str(page.url):
            print("   !! редирект на страницу логина — кука не принята")
            return

        catalog_url = src._extract_catalog_url(page.text)
        print(f"5) торрент-каталог: {catalog_url}")
        catalog = await src._fetch_external(catalog_url, "торрент-каталог")
        print(f"   получен, {len(catalog.text)} байт")

        torrent_url, quality = src._pick_torrent(catalog.text, ["1080", "MP4", "SD"])
        print(f"6) выбрано качество {quality}: {torrent_url}")

        torrent = await src._fetch_external(torrent_url, ".torrent")
        bencode = torrent.content[:1] == b"d"
        print(f"7) .torrent: {len(torrent.content)} байт, bencode: {'да — УСПЕХ' if bencode else 'НЕТ — это не торрент!'}")
        if not bencode:
            print("   первые 200 байт ответа:", torrent.content[:200])
    finally:
        await src.close()


if __name__ == "__main__":
    asyncio.run(main())
