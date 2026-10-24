"""Zero-dependency HTTP client for a remote GenIDtals server.

Use this when the biometric engine runs as a separate service (see
``genidtals serve``); it needs only the standard library, so it can be
vendored into any Python project.
"""

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional, Sequence, Union

ImageLike = Union[str, os.PathLike, bytes]


class GenIDtalsAPIError(Exception):
    def __init__(self, status: int, error: str, message: str):
        super().__init__(f"{status} {error}: {message}")
        self.status, self.error, self.message = status, error, message


def _encode_image(image: ImageLike) -> str:
    data = bytes(image) if isinstance(image, (bytes, bytearray)) else Path(image).read_bytes()
    return base64.b64encode(data).decode()


class GenIDtalsClient:
    """Client for the GenIDtals REST API.

    Args:
        base_url: e.g. ``https://biometrics.internal.example.com``.
        api_key: One of the server's API keys.
        allow_insecure: Permit plain ``http://`` to non-local hosts. The API
            key and biometric images would travel unencrypted.
    """

    def __init__(self, base_url: str, api_key: str, timeout: float = 30.0,
                 allow_insecure: bool = False):
        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("base_url must be an http:// or https:// URL")
        local = parsed.hostname in ("localhost", "127.0.0.1", "::1")
        if parsed.scheme != "https" and not (local or allow_insecure):
            raise ValueError("Use https:// (or pass allow_insecure=True for trusted networks)")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _request(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base_url + path, data=data, method=method)  # noqa: S310
        req.add_header("Authorization", f"Bearer {self.api_key}")
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            # The scheme is restricted to http(s) in __init__.
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
                return json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                payload = json.loads(e.read())
            except ValueError:
                payload = {}
            raise GenIDtalsAPIError(e.code, payload.get("error", "http_error"),
                                    payload.get("message", e.reason)) from None

    @staticmethod
    def _user(user_id: str) -> str:
        return urllib.parse.quote(user_id, safe="")

    def health(self) -> dict:
        return self._request("GET", "/v1/health")

    def info(self) -> dict:
        return self._request("GET", "/v1/info")

    def enroll(self, user_id: str, images: Sequence[ImageLike], overwrite: bool = False) -> dict:
        return self._request("POST", f"/v1/users/{self._user(user_id)}/enroll",
                             {"images": [_encode_image(i) for i in images], "overwrite": overwrite})

    def verify(self, user_id: str, image: ImageLike, frames: Sequence[ImageLike] = ()) -> dict:
        return self._request("POST", f"/v1/users/{self._user(user_id)}/verify",
                             {"image": _encode_image(image), "frames": [_encode_image(f) for f in frames]})

    def status(self, user_id: str) -> dict:
        return self._request("GET", f"/v1/users/{self._user(user_id)}")

    def delete_user(self, user_id: str) -> dict:
        return self._request("DELETE", f"/v1/users/{self._user(user_id)}")

    def unlock(self, user_id: str) -> dict:
        return self._request("POST", f"/v1/users/{self._user(user_id)}/unlock")

    def create_template(self, subject_id: str, images: Sequence[ImageLike]) -> str:
        """Returns the sealed template as a base64 string to store in your own database."""
        body = {"subject_id": subject_id, "images": [_encode_image(i) for i in images]}
        return self._request("POST", "/v1/templates", body)["template"]

    def verify_template(self, subject_id: str, template: str, image: ImageLike,
                        frames: Sequence[ImageLike] = ()) -> dict:
        return self._request("POST", "/v1/templates/verify", {
            "subject_id": subject_id, "template": template, "image": _encode_image(image),
            "frames": [_encode_image(f) for f in frames]})

    def reseal_template(self, subject_id: str, template: str) -> str:
        return self._request("POST", "/v1/templates/reseal",
                             {"subject_id": subject_id, "template": template})["template"]

    def quality(self, image: ImageLike) -> dict:
        return self._request("POST", "/v1/quality", {"image": _encode_image(image)})
