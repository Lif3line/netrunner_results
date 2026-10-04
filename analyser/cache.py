"""Cache successful source GETs until an explicit refresh"""

import json
from datetime import datetime, timezone

import requests

from analyser.store import connect, json_text


class CacheMiss(requests.RequestException):
    """A requested source is not available offline"""


class CachedSession(requests.Session):
    def __init__(self, path, refresh=False, *, offline=False):
        super().__init__()
        self.path = path
        self.refresh = refresh
        self.offline = offline

    def get(self, url, **kwargs):
        request_url = requests.Request("GET", url, params=kwargs.get("params")).prepare().url
        if not self.refresh:
            with connect(self.path) as connection:
                row = connection.execute(
                    "SELECT * FROM http_cache WHERE request_url = ?", (request_url,)
                ).fetchone()
            if row:
                response = requests.Response()
                response.status_code = 200
                response.url = row["response_url"]
                response._content = row["body"]
                response.headers.update(json.loads(row["headers"]))
                response.encoding = row["encoding"]
                return response
        if self.offline:
            raise CacheMiss(f"No cached response for {request_url}")
        response = super().get(url, **kwargs)
        if response.status_code == 200:
            with connect(self.path) as connection:
                connection.execute(
                    "INSERT OR REPLACE INTO http_cache VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        request_url,
                        response.url,
                        response.content,
                        json_text(dict(response.headers)),
                        response.encoding,
                        datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    ),
                )
        return response
