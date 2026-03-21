from enum import Enum


class RouterErrorType(str, Enum):
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    AUTH = "auth"
    SCHEMA_ERROR = "schema_error"
    UPSTREAM_5XX = "upstream_5xx"
    NETWORK = "network"
    BAD_REQUEST = "bad_request"
    UNKNOWN = "unknown"
