"""Domain errors for the service layer.

WHY THIS FILE EXISTS
--------------------
The service layer must stay framework-agnostic — it should not import FastAPI or
raise `HTTPException`. Instead it raises these typed domain errors, and the HTTP
layer maps them to status codes in one place. Each error carries the HTTP status
it should translate to, so the route handler needs no per-type branching.
"""


class UploadError(Exception):
    """Base class for upload-validation failures. Maps to HTTP 400 by default."""

    status_code: int = 400

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class InvalidFileExtension(UploadError):
    """Uploaded file is not a .csv."""


class EmptyFileError(UploadError):
    """Uploaded file has no content."""


class InvalidCsvError(UploadError):
    """File is not valid CSV or is missing required header columns."""


class FileTooLargeError(UploadError):
    """Uploaded file exceeds the configured size limit."""

    status_code = 413
