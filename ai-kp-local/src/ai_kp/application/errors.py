class ApplicationError(Exception):
    """Base class for expected use-case failures."""


class InvalidInputError(ApplicationError):
    """The caller supplied semantically invalid input."""


class ConflictError(ApplicationError):
    """The requested transition conflicts with current durable state."""


class ResourceNotFoundError(ApplicationError):
    """The requested application resource does not exist."""


class AccessDeniedError(ApplicationError):
    """An authenticated caller lacks permission for the operation."""


class UpstreamServiceError(ApplicationError):
    """A configured model or other upstream service failed."""


class UpstreamInvalidResponseError(ApplicationError):
    """A configured model service returned an unusable structured response."""


class DirectorHelpClientDisconnectedError(ApplicationError):
    """The requesting KP disconnected before Need Help completed."""


class DirectorHelpAuditUnavailableError(ApplicationError):
    """Need Help cannot produce an untraceable response."""


class KpSessionEndedError(RuntimeError):
    """Raised when a KP credential expires while an LLM turn is in flight."""
