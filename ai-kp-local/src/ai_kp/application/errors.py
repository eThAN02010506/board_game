class KpSessionEndedError(RuntimeError):
    """Raised when a KP credential expires while an LLM turn is in flight."""
