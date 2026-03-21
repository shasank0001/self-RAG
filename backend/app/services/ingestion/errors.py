from dataclasses import dataclass


@dataclass
class IngestionError(Exception):
    code: str
    message: str
    stage: str
    retriable: bool = False

    def __str__(self) -> str:
        return f"[{self.stage}] {self.code}: {self.message}"


class ParserUnsupportedTypeError(IngestionError):
    def __init__(self, *, message: str) -> None:
        super().__init__(code="unsupported_source_type", message=message, stage="parse", retriable=False)


class ParserExecutionError(IngestionError):
    def __init__(self, *, message: str) -> None:
        super().__init__(code="parse_failed", message=message, stage="parse", retriable=False)


class ExternalDependencyError(IngestionError):
    def __init__(self, *, stage: str, message: str, retriable: bool = True) -> None:
        super().__init__(code="external_dependency_error", message=message, stage=stage, retriable=retriable)
