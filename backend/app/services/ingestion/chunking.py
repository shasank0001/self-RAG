from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Mapping
from uuid import UUID

from app.core.config import Settings
from app.models.item import ItemSourceType


VectorMetadataValue = str | int | float | bool

REQUIRED_METADATA_FIELDS = {
    "item_id",
    "bin_id",
    "source_name",
    "source_type",
    "chunk_index",
    "chunk_id",
    "content_hash",
}


@dataclass(slots=True)
class ChunkRecord:
    chunk_id: str
    chunk_index: int
    text: str
    metadata: dict[str, VectorMetadataValue]


def _build_chunk_id(*, item_id: UUID, chunk_index: int, chunk_text: str) -> str:
    suffix = sha256(chunk_text.encode("utf-8")).hexdigest()[:12]
    return f"{item_id}:{chunk_index}:{suffix}"


def _normalize_metadata_value(value: object) -> VectorMetadataValue:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, str):
        return value
    return str(value)


def normalize_chunk_metadata(raw_metadata: Mapping[str, object]) -> dict[str, VectorMetadataValue]:
    return {
        key: _normalize_metadata_value(value)
        for key, value in raw_metadata.items()
    }


def validate_chunk_metadata(metadata: Mapping[str, object]) -> dict[str, VectorMetadataValue]:
    normalized_metadata = normalize_chunk_metadata(metadata)

    missing = REQUIRED_METADATA_FIELDS.difference(normalized_metadata.keys())
    if missing:
        missing_fields = ", ".join(sorted(missing))
        raise ValueError(f"Chunk metadata is missing required keys: {missing_fields}")

    required_string_fields = ["item_id", "bin_id", "source_name", "source_type", "chunk_id", "content_hash"]
    for field_name in required_string_fields:
        value = normalized_metadata.get(field_name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Chunk metadata field '{field_name}' must be a non-empty string")

    chunk_index = normalized_metadata.get("chunk_index")
    if not isinstance(chunk_index, int) or chunk_index < 0:
        raise ValueError("Chunk metadata field 'chunk_index' must be a non-negative integer")

    return normalized_metadata


def build_chunks(
    *,
    text: str,
    item_id: UUID,
    bin_id: UUID,
    source_name: str,
    source_type: ItemSourceType,
    content_hash: str,
    settings: Settings,
    parser_metadata: dict[str, str | int] | None = None,
) -> list[ChunkRecord]:
    try:
        from langchain_text_splitters import RecursiveCharacterTextSplitter
    except ImportError as exc:  # pragma: no cover - dependency validation path
        raise RuntimeError("langchain-text-splitters is required for chunking") from exc

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
    )
    chunks = splitter.split_text(text)

    records: list[ChunkRecord] = []
    for index, chunk_text in enumerate(chunks):
        chunk_id = _build_chunk_id(item_id=item_id, chunk_index=index, chunk_text=chunk_text)
        raw_metadata: dict[str, object] = {
            "item_id": str(item_id),
            "bin_id": str(bin_id),
            "source_name": source_name,
            "source_type": source_type.value,
            "chunk_index": index,
            "chunk_id": chunk_id,
            "content_hash": content_hash,
            "chunk_text": chunk_text,
        }
        if parser_metadata:
            for key, value in parser_metadata.items():
                metadata_key = "parser_name" if key == "parser" else f"parser_{key}"
                raw_metadata[metadata_key] = value

        normalized_metadata = validate_chunk_metadata(raw_metadata)
        records.append(
            ChunkRecord(
                chunk_id=chunk_id,
                chunk_index=index,
                text=chunk_text,
                metadata=normalized_metadata,
            )
        )
    return records
