"""The shared store the studio web app and `producer sync` both talk to.

The web app records intent -- a note, an upload, a settings request -- and this
machine does the work. The store is the only thing they have in common, so it
is deliberately dumb: paths, bytes, and a listing. One object per event, never
an append to a shared file, so two writers cannot race.

Two backends, one interface. `LocalStore` is a folder, used in development and
by the tests. `BlobStore` is Vercel Blob, reached through `web/scripts/blob.mjs`
so there is exactly one implementation of blob access rather than a second,
hand-rolled REST client here.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = REPO_ROOT / "web"
DEFAULT_LOCAL_DIR = WEB_DIR / ".data"
BLOB_SCRIPT = WEB_DIR / "scripts" / "blob.mjs"

# Either of these in the environment (or web/.env.local) means "use the cloud".
BLOB_ENV_KEYS = ("BLOB_READ_WRITE_TOKEN", "BLOB_STORE_ID")


class StoreError(RuntimeError):
    """The store could not be reached or refused the operation."""


@dataclass(frozen=True)
class Entry:
    """One object in a listing. `stamp` changes whenever the content does."""

    path: str
    stamp: str
    size: int


class Store:
    """What sync needs from a store, whichever backend holds the bytes."""

    name = "store"

    def list(self, prefix: str) -> list[Entry]:
        raise NotImplementedError

    def download(self, path: str, dest: Path) -> bool:
        """Write the object to `dest`. False when it does not exist."""
        raise NotImplementedError

    def upload(self, path: str, source: Path, content_type: str) -> None:
        raise NotImplementedError

    def delete(self, path: str) -> None:
        raise NotImplementedError

    # -- conveniences built on the four primitives --------------------------

    def get_bytes(self, path: str) -> bytes | None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "object"
            return dest.read_bytes() if self.download(path, dest) else None

    def get_json(self, path: str):
        raw = self.get_bytes(path)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise StoreError(f"{path} is not valid JSON: {exc}") from exc

    def put_json(self, path: str, value) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "object.json"
            source.write_text(json.dumps(value, indent=2) + "\n")
            self.upload(path, source, "application/json")


def _safe(path: str) -> str:
    """Store paths are relative and never climb out of the store."""
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    if not parts or any(p in {".", ".."} for p in parts):
        raise StoreError(f"Refusing unsafe store path {path!r}")
    return "/".join(parts)


class LocalStore(Store):
    """A folder on disk. The web app's dev server reads and writes the same one."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.name = f"local folder {self.root}"

    def _at(self, path: str) -> Path:
        return self.root / _safe(path)

    def list(self, prefix: str) -> list[Entry]:
        clean = prefix.strip("/")
        base = self.root / clean if clean else self.root
        if not base.is_dir():
            return []
        found = []
        for file in sorted(base.rglob("*")):
            if not file.is_file() or file.name.startswith(".") or file.name.endswith(".tmp"):
                continue
            stat = file.stat()
            found.append(Entry(
                path=file.relative_to(self.root).as_posix(),
                stamp=str(stat.st_mtime_ns),
                size=stat.st_size,
            ))
        return found

    def download(self, path: str, dest: Path) -> bool:
        source = self._at(path)
        if not source.is_file():
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
        return True

    def upload(self, path: str, source: Path, content_type: str) -> None:
        dest = self._at(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Write beside the target and rename, so the web app never reads half a file.
        staging = dest.with_name(dest.name + ".tmp")
        shutil.copyfile(source, staging)
        staging.replace(dest)

    def delete(self, path: str) -> None:
        self._at(path).unlink(missing_ok=True)


class BlobStore(Store):
    """Vercel Blob, through the one Node script that knows how to talk to it."""

    name = "Vercel Blob"

    def __init__(self, env: dict[str, str] | None = None) -> None:
        if shutil.which("node") is None:
            raise StoreError("`node` is not on PATH, and blob access runs through it.")
        if not BLOB_SCRIPT.exists():
            raise StoreError(f"Missing {BLOB_SCRIPT}.")
        self.env = {**os.environ, **(env or {})}

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        result = subprocess.run(
            ["node", str(BLOB_SCRIPT), *args],
            capture_output=True, text=True, cwd=str(WEB_DIR), env=self.env,
        )
        if result.returncode not in (0, 3):
            detail = (result.stderr or result.stdout).strip().splitlines()
            raise StoreError(f"blob {args[0]} failed: {detail[-1] if detail else 'no output'}")
        return result

    def list(self, prefix: str) -> list[Entry]:
        rows = json.loads(self._run("list", prefix).stdout or "[]")
        return [Entry(path=r["path"], stamp=str(r["stamp"]), size=int(r["size"])) for r in rows]

    def download(self, path: str, dest: Path) -> bool:
        dest.parent.mkdir(parents=True, exist_ok=True)
        # The script runs from web/, so a relative path would point nowhere.
        return self._run("get", _safe(path), str(Path(dest).resolve())).returncode == 0

    def upload(self, path: str, source: Path, content_type: str) -> None:
        self._run("put", _safe(path), str(Path(source).resolve()), content_type)

    def delete(self, path: str) -> None:
        self._run("del", _safe(path))


def read_env_file(path: Path) -> dict[str, str]:
    """Just enough dotenv to find blob credentials `vercel env pull` wrote."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def open_store(local_dir: str | Path | None = None) -> Store:
    """The store sync should use.

    An explicit folder wins. Otherwise blob credentials, in the environment or
    in `web/.env.local`, mean the hosted store; with neither, the same local
    folder `npm run dev` uses.
    """
    explicit = local_dir or os.environ.get("MIXMAX_DATA_DIR")
    if explicit:
        return LocalStore(explicit)

    file_env = read_env_file(WEB_DIR / ".env.local")
    if any(os.environ.get(k) or file_env.get(k) for k in BLOB_ENV_KEYS):
        return BlobStore(env={k: v for k, v in file_env.items() if k not in os.environ})
    return LocalStore(DEFAULT_LOCAL_DIR)
