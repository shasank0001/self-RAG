from __future__ import annotations

import io
import re
from dataclasses import dataclass
from pathlib import Path

from app.models.item import ItemSourceType
from app.services.ingestion.errors import ParserExecutionError, ParserUnsupportedTypeError


def normalize_text(value: str) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    normalized = "\n".join(line.strip() for line in normalized.split("\n"))
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


@dataclass(slots=True)
class ParsePayload:
    source_name: str
    source_type: ItemSourceType
    media_type: str | None
    raw_text: str | None
    file_bytes: bytes | None


@dataclass(slots=True)
class ParsedText:
    normalized_text: str
    metadata: dict[str, str | int]


class BaseParser:
    def parse(self, payload: ParsePayload) -> ParsedText:
        raise NotImplementedError


class RawTextParser(BaseParser):
    def parse(self, payload: ParsePayload) -> ParsedText:
        raw_text = payload.raw_text
        if raw_text is None and payload.file_bytes is not None:
            try:
                raw_text = payload.file_bytes.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ParserExecutionError(message="Unable to decode text file as UTF-8") from exc

        if raw_text is None:
            raise ParserExecutionError(message="No text payload was supplied")

        normalized = normalize_text(raw_text)
        if not normalized:
            raise ParserExecutionError(message="Input text is empty after normalization")

        return ParsedText(
            normalized_text=normalized,
            metadata={
                "parser": "raw_text",
                "character_count": len(normalized),
            },
        )


class PdfParser(BaseParser):
    def parse(self, payload: ParsePayload) -> ParsedText:
        if payload.file_bytes is None:
            raise ParserExecutionError(message="PDF payload is missing file bytes")

        try:
            import fitz  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ParserExecutionError(message="PyMuPDF is not installed") from exc

        try:
            with fitz.open(stream=payload.file_bytes, filetype="pdf") as document:
                pages = [document.load_page(index).get_text("text") for index in range(document.page_count)]
        except Exception as exc:
            raise ParserExecutionError(message="Failed to parse PDF document") from exc

        normalized = normalize_text("\n".join(pages))
        if not normalized:
            raise ParserExecutionError(message="PDF contains no extractable text")

        return ParsedText(
            normalized_text=normalized,
            metadata={
                "parser": "pymupdf",
                "page_count": len(pages),
            },
        )


class DocxParser(BaseParser):
    def parse(self, payload: ParsePayload) -> ParsedText:
        if payload.file_bytes is None:
            raise ParserExecutionError(message="DOCX payload is missing file bytes")

        try:
            from docx import Document  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ParserExecutionError(message="python-docx is not installed") from exc

        try:
            doc = Document(io.BytesIO(payload.file_bytes))
            text_parts = [paragraph.text for paragraph in doc.paragraphs]
        except Exception as exc:
            raise ParserExecutionError(message="Failed to parse DOCX document") from exc

        normalized = normalize_text("\n".join(text_parts))
        if not normalized:
            raise ParserExecutionError(message="DOCX contains no extractable text")

        return ParsedText(
            normalized_text=normalized,
            metadata={
                "parser": "python-docx",
                "paragraph_count": len(text_parts),
            },
        )


class ParserFactory:
    SUPPORTED_UPLOAD_TYPES = {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".txt": "text/plain",
    }

    @classmethod
    def resolve_upload_media_type(cls, source_name: str, media_type: str | None) -> str:
        suffix = Path(source_name).suffix.lower()
        if suffix in cls.SUPPORTED_UPLOAD_TYPES:
            return cls.SUPPORTED_UPLOAD_TYPES[suffix]

        if media_type in cls.SUPPORTED_UPLOAD_TYPES.values():
            return media_type

        raise ParserUnsupportedTypeError(message="Only PDF, DOCX, and TXT uploads are supported")

    @classmethod
    def create_parser(cls, payload: ParsePayload) -> BaseParser:
        if payload.source_type == ItemSourceType.TEXT:
            return RawTextParser()

        media_type = cls.resolve_upload_media_type(payload.source_name, payload.media_type)
        if media_type == "application/pdf":
            return PdfParser()
        if media_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            return DocxParser()
        if media_type == "text/plain":
            return RawTextParser()

        raise ParserUnsupportedTypeError(message="Unable to select parser for the uploaded file")
