import json
import logging

import httpx

log = logging.getLogger(__name__)

# Коды DSM "сессия невалидна" — на них перелогиниваемся один раз.
_SESSION_ERRORS = {105, 106, 107, 119}


class SynologyError(Exception):
    pass


class SynologyClient:
    """Минимальный клиент DSM Web API: логин, Download Station, FileStation."""

    def __init__(self, base_url: str, username: str, password: str):
        self.username = username
        self.password = password
        self._sid: str | None = None
        self._client = httpx.AsyncClient(base_url=base_url, timeout=60)

    async def close(self) -> None:
        await self._client.aclose()

    async def _login(self) -> None:
        r = await self._client.get("/webapi/auth.cgi", params={
            "api": "SYNO.API.Auth", "version": "6", "method": "login",
            "account": self.username, "passwd": self.password,
            "session": "DownloadStation", "format": "sid",
        })
        payload = r.json()
        if not payload.get("success"):
            raise SynologyError(f"Логин в DSM не удался: {payload.get('error')}")
        self._sid = payload["data"]["sid"]

    async def _call(self, path: str, params: dict | None = None,
                    data: dict | None = None, files: dict | None = None,
                    _retry: bool = True) -> dict:
        if self._sid is None:
            await self._login()
        try:
            if data or files:
                r = await self._client.post(path, params={"_sid": self._sid},
                                            data=data, files=files)
            else:
                r = await self._client.get(path, params={**(params or {}), "_sid": self._sid})
        except httpx.HTTPError as e:
            raise SynologyError(f"NAS недоступен: {e}") from e
        payload = r.json()
        if not payload.get("success"):
            code = (payload.get("error") or {}).get("code")
            if code in _SESSION_ERRORS and _retry:
                self._sid = None
                return await self._call(path, params, data, files, _retry=False)
            raise SynologyError(f"Ошибка DSM API {path}: {payload.get('error')}")
        return payload.get("data") or {}

    async def create_task(self, torrent: bytes, filename: str, destination: str) -> str:
        """Добавляет .torrent в очередь DS, возвращает id задачи.

        SYNO.DownloadStation2.Task в отличие от первой версии возвращает task_id.
        Строковые параметры этого API при multipart-запросе должны быть
        JSON-кодированы (в кавычках) — это его документированная причуда.
        """
        data = {
            "api": "SYNO.DownloadStation2.Task",
            "version": "2",
            "method": "create",
            "type": '"file"',
            "file": '["torrent"]',
            "destination": json.dumps(destination),
            "create_list": "false",
        }
        files = {"torrent": (filename, torrent, "application/x-bittorrent")}
        resp = await self._call("/webapi/entry.cgi", data=data, files=files)
        task_ids = resp.get("task_id") or []
        if not task_ids:
            raise SynologyError(f"DS не вернул id задачи: {resp}")
        return task_ids[0]

    async def get_tasks(self, ids: list[str]) -> dict[str, dict]:
        """Статусы задач по id. Отсутствующие в ответе id — задача удалена."""
        if not ids:
            return {}
        resp = await self._call("/webapi/DownloadStation/task.cgi", params={
            "api": "SYNO.DownloadStation.Task", "version": "1", "method": "getinfo",
            "id": ",".join(ids), "additional": "transfer",
        })
        return {t["id"]: t for t in resp.get("tasks", [])}

    async def delete_tasks(self, ids: list[str]) -> None:
        if not ids:
            return
        await self._call("/webapi/DownloadStation/task.cgi", params={
            "api": "SYNO.DownloadStation.Task", "version": "1", "method": "delete",
            "id": ",".join(ids), "force_complete": "false",
        })

    async def ensure_folder(self, destination: str) -> None:
        """Создаёт папку назначения, если её нет. destination — без ведущего слэша."""
        parts = destination.strip("/").split("/")
        if len(parts) < 2:
            return  # общая папка верхнего уровня должна существовать сама
        await self._call("/webapi/entry.cgi", params={
            "api": "SYNO.FileStation.CreateFolder", "version": "2", "method": "create",
            "folder_path": json.dumps(["/" + "/".join(parts[:-1])]),
            "name": json.dumps([parts[-1]]),
            "force_parent": "true",
        })
