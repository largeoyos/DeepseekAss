from __future__ import annotations

import queue
import threading
import uuid
from dataclasses import dataclass, field
from typing import Callable


@dataclass(order=True)
class QueuedAgentTask:
    priority: int
    sequence: int
    task_id: str = field(compare=False)
    read_only: bool = field(compare=False)
    target: Callable = field(compare=False)


class AgentTaskQueue:
    """One serial write lane and a bounded read-only lane."""

    def __init__(self, read_concurrency: int = 2) -> None:
        self._write_queue = queue.PriorityQueue()
        self._read_queue = queue.PriorityQueue()
        self._sequence = 0
        self._closed = threading.Event()
        threading.Thread(target=self._worker, args=(self._write_queue,), daemon=True, name="agent-write-queue").start()
        for index in range(max(1, read_concurrency)):
            threading.Thread(target=self._worker, args=(self._read_queue,), daemon=True, name=f"agent-read-queue-{index}").start()

    def submit(self, target: Callable, *, read_only: bool = False, priority: int = 100) -> str:
        self._sequence += 1
        task_id = f"agent_task_{uuid.uuid4().hex}"
        task = QueuedAgentTask(priority, self._sequence, task_id, read_only, target)
        (self._read_queue if read_only else self._write_queue).put(task)
        return task_id

    def close(self) -> None:
        self._closed.set()

    def _worker(self, work_queue) -> None:
        while not self._closed.is_set():
            try:
                task = work_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                task.target()
            finally:
                work_queue.task_done()
