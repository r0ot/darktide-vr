"""The vault: content-addressed copies of every file the tool has seen.

A blob is named by its SHA-256 and written once: to a temporary name beside
it, flushed, renamed into place, then read back and hashed. Nothing in the
store is ever modified or deleted by an ordinary command, so any state the
tool has recorded can be put back byte for byte.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

CHUNK = 1 << 20


class StoreError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def sha1_file(path: Path) -> str:
    digest = hashlib.sha1()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value) -> None:
    """Atomically, so a crash leaves the old document or the new one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def read_json(path: Path, default=None):
    if not path.is_file():
        return default
    with open(path, encoding="utf-8") as stream:
        return json.load(stream)


class Vault:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.blobs = self.root / "store"
        self.captures = self.root / "captures"
        self.profiles = self.root / "profiles"
        self.journal = self.root / "journal"
        self.history = self.root / "history"
        self.staging = self.root / "staging"
        self.state_path = self.root / "state.json"
        self.library_path = self.root / "library.json"
        self.settings_path = self.root / "settings-slots.json"

    def ensure(self) -> None:
        for directory in (self.blobs, self.captures, self.profiles, self.journal,
                          self.history, self.staging):
            directory.mkdir(parents=True, exist_ok=True)

    # --- blobs --------------------------------------------------------------

    def blob_path(self, digest: str) -> Path:
        return self.blobs / digest[:2] / digest

    def has(self, digest: str) -> bool:
        return self.blob_path(digest).is_file()

    def put_file(self, source: Path, expected: str | None = None) -> str:
        """Store a copy of `source`; returns its SHA-256."""
        source = Path(source)
        self.staging.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=self.staging, prefix="blob-", suffix=".tmp")
        os.close(handle)
        temporary = Path(temporary)
        try:
            digest = hashlib.sha256()
            with open(source, "rb") as reader, open(temporary, "wb") as writer:
                for block in iter(lambda: reader.read(CHUNK), b""):
                    digest.update(block)
                    writer.write(block)
                writer.flush()
                os.fsync(writer.fileno())
            value = digest.hexdigest()
            if expected and value != expected:
                raise StoreError(f"{source} changed while it was stored "
                                 f"(expected {expected}, read {value})")
            target = self.blob_path(value)
            if target.is_file():
                if sha256_file(target) != value:
                    raise StoreError(f"stored blob {value} is damaged")
                return value
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(temporary, target)
            if sha256_file(target) != value:
                raise StoreError(f"stored blob {value} did not read back")
            return value
        finally:
            temporary.unlink(missing_ok=True)

    def put_bytes(self, data: bytes) -> str:
        self.staging.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=self.staging, prefix="bytes-", suffix=".tmp")
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(data)
            return self.put_file(Path(temporary))
        finally:
            Path(temporary).unlink(missing_ok=True)

    def copy_out(self, digest: str, destination: Path) -> None:
        """Write a blob to `destination` atomically and verify it there."""
        source = self.blob_path(digest)
        if not source.is_file():
            raise StoreError(f"blob {digest} is not in the vault")
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".dtprofiles-tmp")
        try:
            shutil.copyfile(source, temporary)
            if sha256_file(temporary) != digest:
                raise StoreError(f"copy of {digest} to {destination} is damaged")
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        if sha256_file(destination) != digest:
            raise StoreError(f"{destination} does not hold {digest} after writing")

    def verify_blob(self, digest: str) -> bool:
        path = self.blob_path(digest)
        return path.is_file() and sha256_file(path) == digest
