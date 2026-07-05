"""erosgen error type and the fail() helper.

ConfigError is the fatal path (strict validation / CLI). It optionally carries
the structured Diagnostic that triggered it so callers can inspect code/location
rather than re-parsing the message string.
"""


class ConfigError(Exception):
    def __init__(self, message, diagnostic=None):
        super().__init__(message)
        self.diagnostic = diagnostic


def fail(msg):
    """Raise a fatal ConfigError with the historical 'erosgen: ' prefix.

    Retained for callers outside the validation sink; model/validate route
    through Diagnostics.error() so the same checks can also be collected
    non-fatally for a GUI.
    """
    raise ConfigError("erosgen: " + msg)
