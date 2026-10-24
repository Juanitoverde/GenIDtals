"""GenIDtals - anatomical biometric authentication SDK.

Quick start::

    from genidtals import BiometricEngine

    engine = BiometricEngine(security_level="high")
    engine.enroll_user("user_12345", ["ref1.jpg", "ref2.jpg", "ref3.jpg"])
    result = engine.authenticate("user_12345", "attempt.jpg")
    if result.authenticated:
        ...
"""

import os

# Cap OpenCV's image decoder before cv2 is first imported, so decompression
# bombs are refused by the decoder rather than after allocating the bitmap.
os.environ.setdefault("OPENCV_IO_MAX_IMAGE_PIXELS", str(64_000_000))

from ._version import ALGORITHM_VERSION, __version__  # noqa: E402
from .calibration import Calibration  # noqa: E402
from .config import SECURITY_LEVELS, EngineConfig  # noqa: E402
from .crypto import KeyProvider, KeyringFile, StaticKeyProvider, decode_key, encode_key, generate_key  # noqa: E402
from .engine import AuthResult, BiometricEngine, EnrollmentResult, Reason  # noqa: E402
from .errors import (  # noqa: E402
                     AlreadyEnrolledError,
                     ConfigurationError,
                     EnrollmentError,
                     GenIDtalsError,
                     ImageError,
                     KeyManagementError,
                     LivenessError,
                     QualityError,
                     StorageError,
                     TemplateError,
)
from .liveness import LivenessResult  # noqa: E402
from .quality import QualityReport  # noqa: E402
from .security import AuditEvent, LockoutStatus  # noqa: E402
from .stores import FileStore, MemoryStore, SQLiteStore, TemplateStore  # noqa: E402

__all__ = [
    "ALGORITHM_VERSION", "AlreadyEnrolledError", "AuditEvent", "AuthResult", "BiometricEngine", "Calibration",
    "ConfigurationError", "EngineConfig", "EnrollmentError", "EnrollmentResult", "FileStore",
    "GenIDtalsError", "ImageError", "KeyManagementError", "KeyProvider", "KeyringFile",
    "LivenessError", "LivenessResult", "LockoutStatus", "MemoryStore", "QualityError",
    "QualityReport", "Reason", "SECURITY_LEVELS", "SQLiteStore", "StaticKeyProvider",
    "StorageError", "TemplateError", "TemplateStore", "__version__", "decode_key",
    "encode_key", "generate_key",
]
