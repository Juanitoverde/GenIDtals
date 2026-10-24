"""GenIDtals - anatomical biometric authentication."""

__version__ = "0.1.0"

from .engine import AuthResult, BiometricEngine, EnrollmentError  # noqa: E402

__all__ = ["AuthResult", "BiometricEngine", "EnrollmentError", "__version__"]
