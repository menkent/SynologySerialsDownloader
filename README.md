# Synology Serials Downloader

Следит за выходом новых серий на торрент-трекерах (пока — LostFilm) и автоматически
ставит их на закачку в Synology Download Station. По-серийный учёт, веб-интерфейс,
статусы подписок (Активна / На паузе / Завершена). Подписка = один сезон = одна папка.

## Деплой на Synology (DSM 7.2+)

1. **Пользователь DSM.** Создайте отдельного пользователя (например `serials-bot`)
   без 2FA, с правами на Download Station и целевую общую папку. Включите
   сервис домашних папок (Control Panel → User & Group → Advanced).
2. **Образ.** Container Manager → Registry → `ghcr.io/menkent/synologyserialsdownloader` → Pull.
   Либо Container Manager → Project с `docker-compose.yml` из этого репозитория.
3. **Контейнер.** Порт `8923:8000`, volume
   `/volume1/homes/serials-bot/SynologySerialsDownloader → /data`,
   переменные окружения:

   | Переменная      | Значение                              |
   |-----------------|---------------------------------------|
   | `SYNO_URL`      | `http://172.17.0.1:5000` (DSM из контейнера) |
   | `SYNO_USERNAME` | `serials-bot`                          |
   | `SYNO_PASSWORD` | пароль этого пользователя              |

4. **LostFilm.** Откройте `http://<NAS>:8923/settings`, вставьте строку Cookie
   (залогиньтесь на lostfilm.tv в браузере → DevTools → Application → Cookies),
   проверьте базовый путь и приоритет качества.

Интерфейс доступен только из локальной сети — порт наружу не пробрасывайте.

## Как это работает

- Раз в N часов (настройка, по умолчанию 12) и по кнопке «Проверить сейчас»
  приложение обходит активные подписки, находит новые серии и для каждой:
  забирает .torrent у источника → создаёт папку (если нужно) → добавляет задачу
  в Download Station с нужным destination.
- Каждые 5 минут опрашивает Download Station: докачалось — серия помечается
  «Скачан»; задача исчезла или упала — «Ошибка» с кнопкой «Повторить».
- Состояние хранится в одном `state.json` на volume (атомарная запись) —
  человекочитаемо, переживает пересоздание контейнера.

## Разработка

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
APP_DATA_DIR=./data SYNO_URL=http://nas:5000 SYNO_USERNAME=serials-bot \
  SYNO_PASSWORD=... uvicorn app.main:app --reload
```

Источники живут в `app/sources/` и реализуют протокол `Source`
(`extract_slug`, `list_episodes`, `fetch_torrent`, `check_auth`) — новый трекер
добавляется новым модулем без изменений ядра.
