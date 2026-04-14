"""Custom exception hierarchy for RCFlow.

All application-specific exceptions inherit from :class:`ApplicationException`
so callers can catch the entire family with a single ``except`` clause when
needed, while still being able to handle specific sub-types precisely.

Each subclass may define a ``description`` class attribute — a short,
human-readable explanation of what the error means.  The base class
``__str__`` includes the class name and description in the formatted message.
"""


class ApplicationException(Exception):  # noqa: N818
    """Base class for all RCFlow custom exceptions.

    Class attributes:
        description: Optional human-readable description of the error type.
            Subclasses should override this with a concise, static string.
            Defaults to ``None`` in the base class.
    """

    description: str | None = None

    def __str__(self) -> str:
        name = type(self).__name__
        msg = super().__str__()
        if self.description:
            return f"{name}: {msg} — {self.description}"
        return f"{name}: {msg}"


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


class SessionError(ApplicationException):
    """Base class for session-related errors."""

    description = "A session operation failed."


class SessionNotFoundError(SessionError):
    """Raised when a session cannot be found in memory or the database."""

    description = "The requested session does not exist in memory or the database."


class InvalidSessionIdError(SessionError):
    """Raised when a session ID string cannot be parsed as a UUID."""

    description = "The provided session ID is not a valid UUID."


class SessionStateError(SessionError):
    """Raised when an operation is invalid for the session's current state."""

    description = "The operation is not permitted in the session's current state."


class SessionAlreadyActiveError(SessionError):
    """Raised when attempting to restore a session that is already in memory."""

    description = "The session is already active in memory and cannot be restored again."


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class ConfigurationError(ApplicationException):
    """Base class for configuration-related errors."""

    description = "A configuration value is missing or invalid."


class DatabaseNotInitializedError(ConfigurationError):
    """Raised when the database engine has not been initialized via ``init_engine()``."""

    description = "The database engine has not been initialized. Call init_engine() first."


class DatabaseNotConfiguredError(ConfigurationError):
    """Raised when a database-dependent operation is attempted but no DB is configured."""

    description = "No database is configured for this operation."


class LLMConfigurationError(ConfigurationError):
    """Raised when the LLM provider or model configuration is invalid."""

    description = "The LLM provider or model configuration is invalid or unsupported."


class ServerConfigurationError(ConfigurationError):
    """Raised when the server configuration (e.g. SSL/WSS) is incomplete or invalid."""

    description = "The server configuration is incomplete or invalid."


# ---------------------------------------------------------------------------
# Executor / subprocess
# ---------------------------------------------------------------------------


class ExecutorError(ApplicationException):
    """Base class for executor (subprocess) errors."""

    description = "An executor subprocess operation failed."


class ExecutorNotStartedError(ExecutorError):
    """Raised when an operation requires a running subprocess that has not been started."""

    description = "The executor subprocess has not been started or its stdin is unavailable."


class ExecutorAlreadyExitedError(ExecutorError):
    """Raised when the executor subprocess has already exited unexpectedly."""

    description = "The executor subprocess has already exited."


class ExecutorRestartError(ExecutorError):
    """Raised when an executor cannot be restarted due to missing state."""

    description = "The executor cannot be restarted because required state from the previous run is missing."


class ExecutorInputError(ExecutorError):
    """Raised when writing to an executor's stdin fails."""

    description = "Failed to write input to the executor subprocess."


class ExecutorUnsupportedOperationError(ExecutorError):
    """Raised when an operation is not supported by a particular executor type."""

    description = "This operation is not supported by the current executor type."


class MissingPromptError(ExecutorError):
    """Raised when a required ``prompt`` parameter is absent from executor input."""

    description = "A 'prompt' parameter is required but was not provided."


# ---------------------------------------------------------------------------
# Tool / loader
# ---------------------------------------------------------------------------


class ToolError(ApplicationException):
    """Base class for tool-related errors."""

    description = "A tool operation failed."


class UnknownToolError(ToolError):
    """Raised when a requested tool name is not registered."""

    description = "The requested tool name is not registered in the tool directory."


class ToolConfigurationError(ToolError):
    """Raised when a tool definition contains invalid or inconsistent configuration."""

    description = "The tool definition contains invalid or inconsistent configuration."


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


class AgentPermissionError(ApplicationException):
    """Base class for interactive permission errors."""

    description = "An interactive permission operation failed."


class InteractivePermissionsNotEnabledError(AgentPermissionError):
    """Raised when a permission resolution is attempted on a session without a permission manager."""

    description = "The session does not have interactive permissions enabled."


class PermissionRequestNotFoundError(AgentPermissionError):
    """Raised when a permission request ID is unknown or has already been resolved."""

    description = "The permission request ID is unknown or has already been resolved."


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


class TaskNotFoundError(ApplicationException):
    """Raised when a task record cannot be found in the database."""

    description = "The requested task does not exist in the database."


class UnknownExecutorTypeError(ApplicationException):
    """Raised when an unrecognised executor type string is encountered."""

    description = "The executor type string is not recognised."


# ---------------------------------------------------------------------------
# Linear integration
# ---------------------------------------------------------------------------


class LinearServiceError(ApplicationException):
    """Raised when the Linear API returns an error or an unexpected response.

    Attributes:
        status_code: The HTTP status code from the Linear API response, if available.
    """

    description = "The Linear API returned an error or unexpected response."

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
