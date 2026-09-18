"""Stable application errors and exit codes."""


class Dune1Error(Exception):
    """Base error shown to CLI callers."""

    exit_code = 8


class ConfigError(Dune1Error):
    exit_code = 2


class TransportError(Dune1Error):
    exit_code = 3


class AuthenticationError(Dune1Error):
    exit_code = 4


class QuotaError(Dune1Error):
    exit_code = 5


class QueryExecutionError(Dune1Error):
    exit_code = 6


class QueryTimeoutError(Dune1Error):
    exit_code = 7


class ApiProtocolError(Dune1Error):
    exit_code = 8
