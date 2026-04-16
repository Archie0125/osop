"""Live-streaming .osoplog writer for host-executed workflows.

When the workflow is executed by a host app (Flask server, desktop GUI,
long-running daemon, etc.) instead of by `osop record`, use `LiveLog` to
stream node events into an `.osoplog.yaml` file as they happen.

The file is flushed on every node boundary, so a crash mid-run still
leaves a partial record with durable forensic value.

Example
-------
    from osop import LiveLog

    log = LiveLog.start("workflow.osop.yaml", trigger="web-gui",
                        output_dir="logs/")

    with log.node("parse-input") as node:
        records = parse(data)
        node.output(rows=len(records))

    with log.node("write-output") as node:
        try:
            write(records)
        except Exception as e:
            node.fail(error=str(e))
            raise

    log.finish()
"""

from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import yaml

from .parser.loader import load_workflow


def _iso_now() -> str:
    """ISO 8601 UTC timestamp with millisecond precision and trailing 'Z'.

    Standardized so that .osoplog files written by LiveLog interleave cleanly
    with those written by `osop log` (transcript parser) and `osop record`,
    none of which emit a local timezone offset.
    """
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class _NodeContext:
    """Handle returned by `LiveLog.node()` — collects outputs / failures."""

    def __init__(self, parent: "LiveLog", record: dict):
        self._parent = parent
        self._record = record
        self._explicit_status: str | None = None
        self._error: str | None = None

    def output(self, **outputs: Any) -> None:
        """Attach key=value outputs to this node's record."""
        existing = self._record.setdefault("outputs", {})
        existing.update(outputs)
        self._parent._flush()

    def fail(self, error: str) -> None:
        """Mark this node as FAILED with an error message."""
        self._explicit_status = "FAILED"
        self._error = error

    def skip(self, reason: str = "") -> None:
        """Mark this node as SKIPPED."""
        self._explicit_status = "SKIPPED"
        if reason:
            self._record.setdefault("outputs", {})["skip_reason"] = reason


class LiveLog:
    """Live-streaming osoplog writer.

    One instance = one execution run = one `.osoplog.yaml` file.
    Thread-safe for sequential node execution (not for concurrent nodes).
    """

    def __init__(
        self,
        workflow_id: str,
        osoplog_path: Path,
        *,
        trigger: str = "manual",
        actor: str = "host-app",
        runtime_agent: str = "custom",
        runtime_model: str = "n/a",
        known_node_ids: set[str] | None = None,
    ):
        self.run_id = str(uuid.uuid4())
        self.workflow_id = workflow_id
        self.path = osoplog_path
        self.started_at = _iso_now()
        self._started_ts = time.time()
        self._trigger = {"type": "manual", "actor": actor, "timestamp": self.started_at, "source": trigger}
        self._runtime = {"agent": runtime_agent, "model": runtime_model}
        self._node_records: list[dict] = []
        self._current_record: dict | None = None
        self._status = "RUNNING"
        self._known_node_ids = known_node_ids
        self._flush()

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------

    @classmethod
    def start(
        cls,
        workflow_path: str | Path,
        *,
        trigger: str = "manual",
        actor: str = "host-app",
        output_dir: str | Path = ".",
        runtime_agent: str = "custom",
        runtime_model: str = "n/a",
    ) -> "LiveLog":
        """Start a new run from a .osop file. Derives workflow_id + node validation."""
        data = load_workflow(str(workflow_path))
        workflow_id = data.get("id", Path(workflow_path).stem)
        known_ids = {n["id"] for n in data.get("nodes", []) if isinstance(n, dict) and "id" in n}

        Path(output_dir).mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        run_short = uuid.uuid4().hex[:8]
        path = Path(output_dir) / f"{ts}-{run_short}.osoplog.yaml"

        return cls(
            workflow_id=workflow_id,
            osoplog_path=path,
            trigger=trigger,
            actor=actor,
            runtime_agent=runtime_agent,
            runtime_model=runtime_model,
            known_node_ids=known_ids,
        )

    # ------------------------------------------------------------------
    # Node execution
    # ------------------------------------------------------------------

    @contextmanager
    def node(self, node_id: str, *, node_type: str = "cli", attempt: int = 1) -> Iterator[_NodeContext]:
        """Context manager wrapping a node's execution.

        On entry: writes a RUNNING record and flushes.
        On success exit: marks COMPLETED (unless node.fail/skip was called).
        On exception: marks FAILED with the exception repr.
        """
        if self._known_node_ids is not None and node_id not in self._known_node_ids:
            raise ValueError(
                f"node_id '{node_id}' not found in workflow '{self.workflow_id}'. "
                f"Known nodes: {sorted(self._known_node_ids)}"
            )
        if self._current_record is not None:
            raise RuntimeError(
                f"Cannot start node '{node_id}': node '{self._current_record['node_id']}' is still open. "
                f"LiveLog only supports sequential nodes."
            )

        record: dict = {
            "node_id": node_id,
            "node_type": node_type,
            "attempt": attempt,
            "status": "RUNNING",
            "started_at": _iso_now(),
        }
        self._current_record = record
        start_ts = time.time()
        self._flush()

        ctx = _NodeContext(self, record)
        try:
            yield ctx
        except Exception as exc:
            record["status"] = "FAILED"
            record["error"] = f"{type(exc).__name__}: {exc}"
            self._close_current(start_ts)
            raise
        else:
            if ctx._explicit_status:
                record["status"] = ctx._explicit_status
                if ctx._error:
                    record["error"] = ctx._error
            else:
                record["status"] = "COMPLETED"
            self._close_current(start_ts)

    def _close_current(self, start_ts: float) -> None:
        assert self._current_record is not None
        self._current_record["ended_at"] = _iso_now()
        self._current_record["duration_ms"] = int((time.time() - start_ts) * 1000)
        self._node_records.append(self._current_record)
        self._current_record = None
        self._flush()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def finish(self, status: str = "COMPLETED") -> Path:
        """Close the log with a terminal status. Returns the osoplog path."""
        if self._current_record is not None:
            raise RuntimeError(
                f"Cannot finish: node '{self._current_record['node_id']}' is still running."
            )
        self._status = status
        self._ended_at = _iso_now()
        self._duration_ms = int((time.time() - self._started_ts) * 1000)
        self._flush()
        return self.path

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def _flush(self) -> None:
        doc = {
            "osoplog_version": "1.0",
            "run_id": self.run_id,
            "workflow_id": self.workflow_id,
            "mode": "live",
            "status": self._status,
            "trigger": self._trigger,
            "started_at": self.started_at,
            "runtime": self._runtime,
            "node_records": self._node_records + (
                [self._current_record] if self._current_record else []
            ),
        }
        if self._status != "RUNNING":
            doc["ended_at"] = getattr(self, "_ended_at", _iso_now())
            doc["duration_ms"] = getattr(self, "_duration_ms", 0)
        with open(self.path, "w", encoding="utf-8") as f:
            yaml.safe_dump(doc, f, allow_unicode=True, sort_keys=False)


__all__ = ["LiveLog"]
