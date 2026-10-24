"""Exception hierarchy. Catch ``GenIDtalsError`` to handle every SDK error."""


class GenIDtalsError(Exception):
    """Base class for all GenIDtals errors."""


class ImageError(GenIDtalsError, ValueError):
    """The input could not be decoded or contains no usable region of interest."""


class QualityError(GenIDtalsError):
    """The capture is decodable but too poor to enroll or verify reliably."""

    def __init__(self, message: str, report=None):
        super().__init__(message)
        self.report = report


class LivenessError(GenIDtalsError):
    """A presentation attack was suspected during enrollment."""

    def __init__(self, message: str, result=None):
        super().__init__(message)
        self.result = result


class EnrollmentError(GenIDtalsError):
    """Enrollment was refused (too few images, inconsistent references, ...)."""


class AlreadyEnrolledError(EnrollmentError):
    """The user already has templates and ``overwrite`` was not requested."""


class TemplateError(GenIDtalsError):
    """A sealed template failed authentication, decryption or version checks."""


class KeyManagementError(GenIDtalsError):
    """Key material is missing, malformed or does not match the data."""


class StorageError(GenIDtalsError):
    """The template store could not be read or written."""


class ConfigurationError(GenIDtalsError, ValueError):
    """Invalid engine configuration."""
