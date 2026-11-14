"""httpx.Client の、このリポジトリが実際に呼ぶ分だけ（実験）。

研究スクリプトは ``httpx.Client(headers=..., timeout=45, follow_redirects=True)`` を
作り、QueRyu の PoliteFetcher が ``get(url, headers=)`` と ``status_code`` /
``content`` / ``headers.get`` / ``raise_for_status`` だけを使う。中身は urllib。
HTTP/2、ストリーム、認証、cookies、httpx.Timeout オブジェクトは扱わない。
"""
from __future__ import annotations

import urllib.error
import urllib.request


class HTTPStatusError(Exception):
    """``raise_for_status`` が 4xx/5xx で出す。response.status_code を持つ。"""

    def __init__(self, response: "Response"):
        self.response = response
        if response.status_code >= 500:
            kind = "Server error"
        elif response.status_code >= 400:
            kind = "Client error"
        elif response.status_code >= 300:
            kind = "Redirect response"
        else:
            kind = "Invalid status code"
        super().__init__(f"{kind} '{response.status_code}' for url {response.url}")


class _Headers:
    """大文字小文字を区別しない ``.get``。PoliteFetcher は etag と last-modified だけ読む。"""

    def __init__(self, headers):
        data: dict[str, str] = {}
        items = headers.items() if headers is not None and hasattr(headers, "items") else ()
        for key, value in items:
            folded = str(key).lower()
            if folded not in data and value is not None:
                data[folded] = str(value)
        self._data = data

    def get(self, key, default=None):
        return self._data.get(str(key).lower(), default)


class Response:
    def __init__(self, status_code: int, content: bytes, headers, url: str):
        self.status_code = int(status_code)
        self.content = bytes(content)
        self.headers = headers if isinstance(headers, _Headers) else _Headers(headers)
        self.url = url

    def raise_for_status(self) -> None:
        # httpx は 2xx 以外（リダイレクトを含む）で出す。PoliteFetcher は 304 と 404 では呼ばない。
        if not 200 <= self.status_code <= 299:
            raise HTTPStatusError(self)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _merge(base: dict, extra) -> dict:
    folded: dict[str, tuple[str, str]] = {}
    for key, value in list(base.items()) + list((extra or {}).items()):
        folded[str(key).lower()] = (str(key), str(value))
    return {key: value for key, value in folded.values()}


def _timeout(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"httpx_min の timeout は秒の数だけ: {value!r}")
    return float(value)


class Client:
    def __init__(self, *, headers=None, timeout=5, follow_redirects=False):
        self.headers = dict(headers or {})
        self.timeout = _timeout(5 if timeout is None else timeout)
        self.follow_redirects = bool(follow_redirects)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def close(self) -> None:
        return None

    def _opener(self):
        if self.follow_redirects:
            return urllib.request.build_opener()
        return urllib.request.build_opener(_NoRedirect)

    def get(self, url: str, *, headers=None) -> Response:
        request = urllib.request.Request(url, headers=_merge(self.headers, headers), method="GET")
        try:
            with self._opener().open(request, timeout=self.timeout) as response:
                return Response(response.status, response.read(), response.headers, response.geturl())
        except urllib.error.HTTPError as error:
            body = error.read() if error.fp is not None else b""
            return Response(error.code, body, error.headers, getattr(error, "url", None) or url)
