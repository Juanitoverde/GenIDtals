"""REST API server, for integrating from any language.

Install with ``pip install "genidtals[server]"`` and run ``genidtals serve``.
Images and templates travel as base64 strings in JSON bodies. Every
endpoint except ``/v1/health`` requires an API key, sent as
``Authorization: Bearer <key>`` or ``X-API-Key: <key>``.

The server is meant to sit behind your application backend (which owns
user sessions) and a TLS-terminating reverse proxy - not to be exposed to
end users directly.
"""

import base64
import binascii
import hashlib
import hmac
import logging
import os
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ._version import ALGORITHM_VERSION, __version__
from .engine import BiometricEngine
from .errors import (
    AlreadyEnrolledError,
    ConfigurationError,
    EnrollmentError,
    GenIDtalsError,
    ImageError,
    LivenessError,
    QualityError,
    TemplateError,
)

log = logging.getLogger("genidtals.server")

DEFAULT_MAX_BODY = 64 * 1024 * 1024
MAX_IMAGES = 16
MAX_FRAMES = 8


# ----------------------------------------------------------------- schemas

class EnrollRequest(BaseModel):
    images: list[str] = Field(..., min_length=1, max_length=MAX_IMAGES,
                              description="Base64-encoded reference images (PNG, JPEG, ...)")
    overwrite: bool = False


class VerifyRequest(BaseModel):
    image: str = Field(..., description="Base64-encoded capture")
    frames: list[str] = Field(default_factory=list, max_length=MAX_FRAMES,
                              description="Optional extra burst frames for motion liveness")


class TemplateCreateRequest(BaseModel):
    subject_id: str = Field(..., min_length=1, max_length=256)
    images: list[str] = Field(..., min_length=1, max_length=MAX_IMAGES)


class TemplateVerifyRequest(VerifyRequest):
    subject_id: str = Field(..., min_length=1, max_length=256)
    template: str = Field(..., description="Base64 sealed template from POST /v1/templates")


class TemplateResealRequest(BaseModel):
    subject_id: str = Field(..., min_length=1, max_length=256)
    template: str


class QualityRequest(BaseModel):
    image: str


# ----------------------------------------------------------------- helpers

def _b64(data: str, what: str) -> bytes:
    if "," in data[:64] and data.startswith("data:"):
        data = data.split(",", 1)[1]  # accept data: URLs
    try:
        return base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        detail = {"error": "invalid_base64", "message": f"{what} is not valid base64"}
        raise HTTPException(422, detail) from None


def _error_response(exc: GenIDtalsError) -> JSONResponse:
    if isinstance(exc, AlreadyEnrolledError):
        status, code = 409, "already_enrolled"
    elif isinstance(exc, QualityError):
        status, code = 422, "low_quality"
    elif isinstance(exc, LivenessError):
        status, code = 422, "liveness_failed"
    elif isinstance(exc, (EnrollmentError, ImageError, ConfigurationError)):
        status, code = 422, type(exc).__name__.replace("Error", "").lower() or "invalid"
    elif isinstance(exc, TemplateError):
        status, code = 400, "invalid_template"
    else:
        # Storage and key errors: log details, return nothing internal.
        log.error("internal error: %s", exc)
        return JSONResponse({"error": "internal_error", "message": "Internal error"}, status_code=500)
    return JSONResponse({"error": code, "message": str(exc)}, status_code=status)


class _BodyLimit:
    """Reject request bodies over a size limit, including chunked uploads.

    The body is read (up to the limit) before the app sees it, so an
    oversized request is refused without being parsed.
    """

    def __init__(self, app, limit: int):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        for name, value in scope.get("headers", []):
            if name == b"content-length" and (not value.isdigit() or int(value) > self.limit):
                return await self._reject(send)
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                return  # client disconnected
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self.limit:
                return await self._reject(send)
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        body, replayed = b"".join(chunks), False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

    @staticmethod
    async def _reject(send):
        body = b'{"error":"payload_too_large","message":"Request body too large"}'
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


def load_api_keys() -> list[str]:
    keys = [k.strip() for k in os.environ.get("GENIDTALS_API_KEYS", "").split(",") if k.strip()]
    path = os.environ.get("GENIDTALS_API_KEYS_FILE")
    if path:
        with open(path) as f:
            keys += [line.strip() for line in f if line.strip() and not line.startswith("#")]
    return keys


# --------------------------------------------------------------------- app

def create_app(engine: BiometricEngine, api_keys: Optional[list[str]] = None, *,
               allow_unauthenticated: bool = False, max_body_bytes: int = DEFAULT_MAX_BODY,
               docs: bool = False) -> FastAPI:
    """Build the ASGI app. ``api_keys`` defaults to ``$GENIDTALS_API_KEYS`` / ``$GENIDTALS_API_KEYS_FILE``."""
    keys = api_keys if api_keys is not None else load_api_keys()
    short = [k for k in keys if len(k) < 24]
    if short:
        raise ConfigurationError("API keys must be at least 24 characters (use `genidtals keygen --api-key`)")
    if not keys and not allow_unauthenticated:
        raise ConfigurationError("No API keys configured; set GENIDTALS_API_KEYS or pass api_keys")
    digests = [hashlib.sha256(k.encode()).digest() for k in keys]

    app = FastAPI(
        title="GenIDtals API", version=__version__,
        description="Anatomical biometric enrollment and verification.",
        docs_url="/docs" if docs else None, redoc_url=None,
        openapi_url="/openapi.json" if docs else None,
    )
    app.state.engine = engine

    def require_key(authorization: Optional[str] = Header(None),
                    x_api_key: Optional[str] = Header(None)):
        if not digests:
            return
        token = x_api_key
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
        if token:
            d = hashlib.sha256(token.encode()).digest()
            # Compare against every key so timing does not reveal which matched.
            if sum(hmac.compare_digest(d, k) for k in digests):
                return
        raise HTTPException(401, {"error": "unauthorized", "message": "Missing or invalid API key"},
                            headers={"WWW-Authenticate": "Bearer"})

    auth = [Depends(require_key)]

    @app.exception_handler(GenIDtalsError)
    async def _sdk_error(request: Request, exc: GenIDtalsError):
        return _error_response(exc)

    @app.exception_handler(HTTPException)
    async def _http_error(request: Request, exc: HTTPException):
        detail = exc.detail
        if not isinstance(detail, dict):
            detail = {"error": "http_error", "message": str(detail)}
        return JSONResponse(detail, status_code=exc.status_code, headers=exc.headers)

    @app.middleware("http")
    async def _no_store(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/v1/health")
    def health():
        return {"status": "ok", "version": __version__}

    @app.get("/v1/info", dependencies=auth)
    def info():
        return {"version": __version__, "algorithm": ALGORITHM_VERSION,
                "security_level": engine.profile.name, "enrolled": engine.enrolled_count(),
                "calibration": engine.calibration.source}

    @app.post("/v1/users/{user_id:path}/enroll", dependencies=auth)
    def enroll(user_id: str, body: EnrollRequest):
        images = [_b64(img, f"images[{i}]") for i, img in enumerate(body.images)]
        r = engine.enroll_user(user_id, images, overwrite=body.overwrite)
        return {"user_id": r.user_id, "references": r.references, "quality": r.quality}

    @app.post("/v1/users/{user_id:path}/verify", dependencies=auth)
    def verify(user_id: str, body: VerifyRequest):
        frames = [_b64(f, f"frames[{i}]") for i, f in enumerate(body.frames)]
        return engine.authenticate(user_id, _b64(body.image, "image"), frames).to_dict()

    @app.get("/v1/users/{user_id:path}", dependencies=auth)
    def user_status(user_id: str):
        st = engine.lockout_status(user_id)
        return {"user_id": user_id, "enrolled": engine.is_enrolled(user_id),
                "locked": st.locked, "locked_until": st.locked_until}

    @app.delete("/v1/users/{user_id:path}", dependencies=auth)
    def delete_user(user_id: str):
        return {"user_id": user_id, "deleted": engine.delete_user(user_id)}

    @app.post("/v1/users/{user_id:path}/unlock", dependencies=auth)
    def unlock(user_id: str):
        engine.unlock_user(user_id)
        return {"user_id": user_id, "unlocked": True}

    @app.post("/v1/templates", dependencies=auth)
    def create_template(body: TemplateCreateRequest):
        images = [_b64(img, f"images[{i}]") for i, img in enumerate(body.images)]
        blob = engine.create_template(body.subject_id, images)
        return {"subject_id": body.subject_id, "template": base64.b64encode(blob).decode()}

    @app.post("/v1/templates/verify", dependencies=auth)
    def verify_template(body: TemplateVerifyRequest):
        frames = [_b64(f, f"frames[{i}]") for i, f in enumerate(body.frames)]
        return engine.verify_template(body.subject_id, _b64(body.template, "template"),
                                      _b64(body.image, "image"), frames).to_dict()

    @app.post("/v1/templates/reseal", dependencies=auth)
    def reseal_template(body: TemplateResealRequest):
        blob = engine.reseal_template(body.subject_id, _b64(body.template, "template"))
        return {"subject_id": body.subject_id, "template": base64.b64encode(blob).decode()}

    @app.post("/v1/quality", dependencies=auth)
    def quality(body: QualityRequest):
        return engine.assess_quality(_b64(body.image, "image")).to_dict()

    return _BodyLimit(app, max_body_bytes) if max_body_bytes else app


def openapi_schema(engine: Optional[BiometricEngine] = None) -> dict:
    """The OpenAPI document, for generating clients in other languages."""
    app = create_app(engine or BiometricEngine.ephemeral(), api_keys=[], allow_unauthenticated=True,
                     max_body_bytes=0, docs=True)
    schema = app.openapi()
    schema.setdefault("components", {}).setdefault("securitySchemes", {})["bearer"] = {
        "type": "http", "scheme": "bearer"}
    schema["security"] = [{"bearer": []}]
    return schema
