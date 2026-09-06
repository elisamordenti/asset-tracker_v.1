"""Storage abstraction for the document archive (filed compliance documents).

Same injectable-Protocol pattern as DBClient (database.py) and LLMClient
(extraction.py): rule evaluation and the upload pipeline are written and
tested against this narrow interface, never against a filesystem or the
`supabase` package directly. Two implementations exist:

- LocalDiskArchive -- what the CLI uses. Identical behavior to the direct
  filesystem access rules.py and intake.py used to do before this module
  existed, so the CLI's fully-offline workflow is unchanged.
- SupabaseStorageArchive -- what the Streamlit app uses, since a hosted app
  has no durable local disk to file documents into (uploads and any
  filesystem writes are lost on restart/redeploy on most hosting).

A `directory` in this module is a storage-path prefix, not literally an OS
folder -- the exact same value that already appears in a document_on_file
rule's YAML `directory` param works unchanged against either backend, since
Supabase Storage keys are just "/"-delimited strings, not real folders.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class DocumentArchive(Protocol):
    def list_filenames(self, directory: str) -> set[str]:
        """Every filename currently filed under `directory`. Callers should
        call this once per unique directory and reuse the result, rather
        than once per asset per rule -- see validator.py."""
        ...

    def upload(self, directory: str, filename: str, content: bytes) -> None: ...

    def read(self, directory: str, filename: str) -> bytes: ...

    def delete(self, directory: str, filename: str) -> None: ...


@dataclass
class LocalDiskArchive:
    """Backs the CLI's local-only workflow."""

    def list_filenames(self, directory: str) -> set[str]:
        path = Path(directory)
        if not path.exists():
            return set()
        return {f.name for f in path.iterdir() if f.is_file()}

    def upload(self, directory: str, filename: str, content: bytes) -> None:
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        (path / filename).write_bytes(content)

    def read(self, directory: str, filename: str) -> bytes:
        return (Path(directory) / filename).read_bytes()

    def delete(self, directory: str, filename: str) -> None:
        (Path(directory) / filename).unlink(missing_ok=True)


def build_supabase_storage_archive(url: str, key: str, bucket: str) -> DocumentArchive:
    """The real DocumentArchive, backed by a Supabase Storage bucket. Only
    imports `supabase` when actually called, so the test suite never needs
    it installed or a live project."""
    from supabase import create_client

    return SupabaseStorageArchive(create_client(url, key), bucket)


@dataclass
class SupabaseStorageArchive:
    _raw: Any
    bucket: str

    def _files(self):
        return self._raw.storage.from_(self.bucket)

    def list_filenames(self, directory: str) -> set[str]:
        entries = self._files().list(directory)
        return {e["name"] for e in entries}

    def upload(self, directory: str, filename: str, content: bytes) -> None:
        self._files().upload(f"{directory}/{filename}", content, {"upsert": "true"})

    def read(self, directory: str, filename: str) -> bytes:
        return self._files().download(f"{directory}/{filename}")

    def delete(self, directory: str, filename: str) -> None:
        self._files().remove([f"{directory}/{filename}"])
