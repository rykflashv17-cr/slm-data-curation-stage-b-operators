"""Audited whole-source deletion; no video or MCAP rewriting takes place here."""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class UnsafeSourceError(ValueError):
    """The selected pathname cannot safely be used for destructive processing."""


class AuditWriteError(OSError):
    """Deletion must stop because a durable audit event could not be written."""


def _absolute(path: Path) -> Path:
    # Keep symlinks visible. Path.resolve() would conceal a symlink input.
    path = Path(path).expanduser()
    return path if path.is_absolute() else Path.cwd() / path


def _open_directory(path: Path) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(path.anchor, flags)
    try:
        for part in path.parts[1:]:
            try:
                next_fd = os.open(part, flags, dir_fd=fd)
            except OSError as exc:
                raise UnsafeSourceError(f"directory is missing, not a directory, or a symlink: {path}") from exc
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def _open_parent(path: Path, input_root: Path) -> tuple[int, str]:
    path, root = _absolute(path), _absolute(input_root)
    # Validate the original root spelling before normalizing benign '..'
    # components for containment. A symlink followed by '..' must stay visible.
    root_fd = _open_directory(root)
    os.close(root_fd)
    try:
        rel = Path(os.path.normpath(path)).relative_to(Path(os.path.normpath(root)))
    except ValueError as exc:
        raise UnsafeSourceError(f"source is outside input_root: {path}") from exc
    if not rel.parts or path.suffix != ".mcap":
        raise UnsafeSourceError(f"source must be an MCAP file below input_root: {path}")
    return _open_directory(path.parent), path.name


def _identity(st: os.stat_result) -> dict[str, int]:
    return {key: int(getattr(st, key)) for key in ("st_dev", "st_ino", "st_size", "st_mtime_ns")}


def capture_source_identity(path: Path, input_root: Path) -> dict[str, int]:
    """lstat a regular MCAP without following its path or parent symlinks."""
    fd, name = _open_parent(path, input_root)
    try:
        st = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if not stat.S_ISREG(st.st_mode):
            raise UnsafeSourceError(f"source is not a regular MCAP (symlinks are never deleted): {path}")
        return _identity(st)
    finally:
        os.close(fd)


def fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_json_durable(path: Path, payload: Any) -> None:
    """Create a report exactly once and durably finish it before deletion."""
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    fsync_directory(path.parent)


class _EventLog:
    def __init__(self, path: Path):
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        self.fd = os.open(path, flags, 0o600)
        try:
            os.fsync(self.fd)
            fsync_directory(path.parent)
        except BaseException:
            os.close(self.fd)
            raise

    def append(self, payload: dict[str, Any]) -> None:
        event = {"event_time_utc": datetime.now(timezone.utc).isoformat(), **payload}
        try:
            data = (json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
            view = memoryview(data)
            while view:
                size = os.write(self.fd, view)
                if size <= 0:
                    raise OSError("short audit write")
                view = view[size:]
            os.fsync(self.fd)
        except Exception as exc:
            raise AuditWriteError("Cannot durably write deletion audit; deletion stopped") from exc

    def close(self) -> None:
        os.close(self.fd)


def delete_rejected(
    plan_records: list[dict[str, Any]], *, input_root: Path, events_path: Path,
) -> list[dict[str, Any]]:
    """Apply only rejected, identity-checked original paths in a persisted plan.

    Callers must finish decisions.jsonl and deletion_plan.json before calling.
    This helper writes a durable intent before each unlink and a result after it.
    Existing audit logs are refused rather than overwritten or resumed implicitly.
    """
    log = _EventLog(events_path)
    results: list[dict[str, Any]] = []
    try:
        for record in plan_records:
            if record.get("status") != "rejected":
                continue
            source = Path(record["source"])
            base = {"episode_index": record.get("episode_index"), "source": str(source)}
            expected = record.get("source_identity")
            if not record.get("deletion_eligible", False) or not expected:
                result = {**base, "status": "skipped", "reason": record.get("deletion_blocked_reason") or "source identity unavailable"}
                log.append({"event": "deletion_skipped", **result})
                results.append(result)
                continue
            parent_fd = None
            try:
                parent_fd, name = _open_parent(source, input_root)
                st = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                if not stat.S_ISREG(st.st_mode):
                    raise UnsafeSourceError("source changed to a symlink or non-regular file")
                actual = _identity(st)
                if actual != expected:
                    raise UnsafeSourceError("source identity changed since quality read")
                log.append({"event": "delete_intent", **base, "source_identity": actual})
                # Recheck after the audit write, immediately before unlinking.
                latest = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                if not stat.S_ISREG(latest.st_mode) or _identity(latest) != expected:
                    raise UnsafeSourceError("source changed immediately before deletion")
                os.unlink(name, dir_fd=parent_fd)
                sync_error = None
                try:
                    os.fsync(parent_fd)
                except OSError as exc:
                    sync_error = f"{type(exc).__name__}: {exc}"
                result = {**base, "status": "deleted", "deleted_bytes": actual["st_size"]}
                if sync_error:
                    result["source_directory_sync_error"] = sync_error
                results.append(result)
                log.append({"event": "delete_complete", **result})
            except AuditWriteError:
                raise
            except (OSError, UnsafeSourceError) as exc:
                result = {**base, "status": "skipped", "reason": f"{type(exc).__name__}: {exc}"}
                log.append({"event": "deletion_skipped", **result})
                results.append(result)
            finally:
                if parent_fd is not None:
                    os.close(parent_fd)
        log.append({"event": "deletion_batch_complete", "deleted": sum(x["status"] == "deleted" for x in results), "skipped": sum(x["status"] == "skipped" for x in results)})
    except AuditWriteError as exc:
        # An unlink may have succeeded just before a failed result-event fsync.
        # Preserve known outcomes for the caller's separate summary file.
        exc.completed_results = list(results)
        raise
    finally:
        log.close()
    return results
