"""On-node progress reporting built on the stock ComfyUI progress channels.

``PromptServer.send_progress_text`` writes the caption under a running node and
``comfy.utils.ProgressBar`` fills the bar beside it. Both are addressed by node
id, so no custom frontend extension is needed.
"""

from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)

TEXT_MIN_INTERVAL = 0.25

NOTICES_EVENT = "minimax_h3_rewriter.notices"


def announce(node_id, findings, kind: str = "notice") -> None:
    """Hand findings to the frontend, which shows them as a toast.

    The caption under a node holds the full story, but it is a few lines tall
    and the person is usually watching the other end of the graph; the toast
    is what makes a finding impossible to miss. ``findings`` is a list of
    ``(level, message)`` pairs or of objects carrying ``level`` and
    ``message`` -- the self-check's Issue is one. ``kind`` names the toast:
    ``check`` for the self-check's reading of an answer, ``notice`` for the
    nodes' own warnings. Fire-and-forget: a frontend that is not listening
    loses nothing but the toast.
    """
    if node_id is None or not findings:
        return
    issues = []
    for entry in findings:
        if isinstance(entry, (tuple, list)):
            level, message = entry
        else:
            level, message = entry.level, entry.message
        issues.append({"level": str(level), "message": str(message)})
    try:
        from server import PromptServer

        PromptServer.instance.send_sync(
            NOTICES_EVENT, {"node": str(node_id), "kind": kind, "issues": issues}
        )
    except Exception:
        log.debug("[minimax_h3_rewriter.progress] could not announce the findings", exc_info=True)


def format_size(num_bytes: float) -> str:
    if num_bytes >= 1024 ** 3:
        return f"{num_bytes / 1024 ** 3:.2f} GB"
    if num_bytes >= 1024 ** 2:
        return f"{num_bytes / 1024 ** 2:.1f} MB"
    if num_bytes >= 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{int(num_bytes)} B"


def format_duration(seconds: float) -> str:
    if seconds < 0 or seconds != seconds or seconds == float("inf"):
        return "--:--"
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


class NodeProgress:
    """The caption and the fill of the bar under one executing node.

    ComfyUI draws the bar itself for as long as a node is running -- the
    frontend builds the container and takes the fill width from the percentage
    the backend reports. Reporting nothing therefore does not remove the bar, it
    leaves an empty trough, which reads as a stall. So the number goes to the
    bar and the words go to the caption, and neither repeats the other: the
    caption carries size, speed and ETA, the bar carries the fraction.
    """

    def __init__(self, node_id, total: float = 1.0):
        self.node_id = str(node_id) if node_id is not None else None
        self.total = max(float(total), 1.0)
        self._server = None
        self._bar = None
        self._last_text = ""
        self._last_text_at = 0.0

        if self.node_id is None:
            return

        try:
            from server import PromptServer

            self._server = PromptServer.instance
        except Exception:
            log.debug("[minimax_h3_rewriter.NodeProgress] prompt server unavailable", exc_info=True)

    def _ensure_bar(self):
        if self._bar is not None or self.node_id is None:
            return self._bar
        try:
            from comfy.utils import ProgressBar

            self._bar = ProgressBar(self.total, node_id=self.node_id)
        except Exception:
            log.debug("[minimax_h3_rewriter.NodeProgress] progress bar unavailable", exc_info=True)
        return self._bar

    def set_total(self, total: float) -> None:
        self.total = max(float(total), 1.0)
        bar = self._ensure_bar()
        if bar is not None:
            bar.total = self.total

    def update(self, value: float, text: str | None = None) -> None:
        bar = self._ensure_bar()
        if bar is not None:
            try:
                bar.update_absolute(max(0.0, min(float(value), self.total)), self.total)
            except Exception:
                log.debug("[minimax_h3_rewriter.NodeProgress.update] bar update failed", exc_info=True)
        if text is not None:
            self.text(text)

    def ratio(self, fraction: float, text: str | None = None) -> None:
        self.update(self.total * max(0.0, min(1.0, fraction)), text)

    def text(self, message: str, force: bool = False) -> None:
        if self._server is None or self.node_id is None:
            return
        now = time.monotonic()
        if not force and message == self._last_text:
            return
        if not force and now - self._last_text_at < TEXT_MIN_INTERVAL:
            return
        self._last_text = message
        self._last_text_at = now
        try:
            self._server.send_progress_text(message, self.node_id)
        except Exception:
            log.debug("[minimax_h3_rewriter.NodeProgress.text] send failed", exc_info=True)

    def finish(self, message: str | None = None) -> None:
        if message is not None:
            self.text(message, force=True)


class TransferReporter:
    """Turns byte counts into a human caption."""

    def __init__(self, progress: NodeProgress, total_bytes: int, title: str):
        self.progress = progress
        self.total_bytes = max(int(total_bytes), 1)
        self.title = title
        self.started_at = time.monotonic()
        self.baseline = None
        self.progress.set_total(self.total_bytes)

    def set_total(self, total_bytes: int) -> None:
        self.total_bytes = max(int(total_bytes), 1)
        self.progress.set_total(self.total_bytes)

    def __call__(self, transferred: int, current_name: str) -> None:
        if self.baseline is None:
            self.baseline = transferred
            self.started_at = time.monotonic()
        elapsed = max(time.monotonic() - self.started_at, 1e-6)
        speed = (transferred - self.baseline) / elapsed
        remaining = (self.total_bytes - transferred) / speed if speed > 0 else float("inf")
        caption = (
            f"{self.title}\n"
            f"{current_name}\n"
            f"{format_size(transferred)} / {format_size(self.total_bytes)}"
            f" · {format_size(speed)}/s · ETA {format_duration(remaining)}"
        )
        self.progress.update(transferred, caption)
