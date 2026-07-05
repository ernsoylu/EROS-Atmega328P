"""erosgen error type and the fail() helper used across validation."""


class ConfigError(Exception):
    pass


def fail(msg):
    raise ConfigError("erosgen: " + msg)
