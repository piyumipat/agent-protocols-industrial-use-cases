"""Application safety boundary for at-most-once task execution."""

from dataclasses import dataclass
from enum import StrEnum


class TaskState(StrEnum):
    AWARDED = "awarded"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class TaskRecord:
    executor_id: str
    state: TaskState


class TaskLedger:
    def __init__(self) -> None:
        self._tasks: dict[str, TaskRecord] = {}

    def accept(self, task_id: str, executor_id: str) -> bool:
        if task_id in self._tasks:
            return False
        self._tasks[task_id] = TaskRecord(executor_id, TaskState.AWARDED)
        return True

    def complete(self, task_id: str, executor_id: str) -> None:
        record = self._tasks.get(task_id)
        if record != TaskRecord(executor_id, TaskState.AWARDED):
            raise ValueError("task is not awarded to this executor")
        self._tasks[task_id] = TaskRecord(executor_id, TaskState.COMPLETED)

    def get(self, task_id: str) -> TaskRecord | None:
        return self._tasks.get(task_id)

