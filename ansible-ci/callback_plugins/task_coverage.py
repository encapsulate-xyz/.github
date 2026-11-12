"""Records every task that runs, for check_task_coverage.py."""

import json
import os

from ansible.plugins.callback import CallbackBase

DOCUMENTATION = """
name: task_coverage
type: aggregate
short_description: Records every task that runs
description:
  - Appends one JSON line per task result to the file in TASK_COVERAGE_FILE.
  - Paths are relative to the project root (MOLECULE_PROJECT_DIRECTORY, or the working directory),
    so files from different checkouts can be combined. Files outside it keep their absolute path.
requirements:
  - Enable it in callbacks_enabled
"""


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "aggregate"
    CALLBACK_NAME = "task_coverage"
    CALLBACK_NEEDS_ENABLED = True

    def __init__(self):
        super().__init__()
        self._output = os.environ.get("TASK_COVERAGE_FILE")
        self._root = os.path.realpath(os.environ.get("MOLECULE_PROJECT_DIRECTORY") or os.getcwd())

    def _record(self, task, status):
        if not self._output or not task.get_path():
            return
        path, _, line = task.get_path().rpartition(":")
        path = os.path.realpath(path)
        if path.startswith(self._root + os.sep):
            path = os.path.relpath(path, self._root)
        with open(self._output, "a", encoding="utf-8") as output:
            output.write(json.dumps({"path": path, "line": int(line), "name": task.get_name(), "status": status}) + "\n")

    def v2_runner_on_ok(self, result):
        self._record(result._task, "changed" if result._result.get("changed") else "ok")

    def v2_runner_on_failed(self, result, ignore_errors=False):
        self._record(result._task, "failed")

    def v2_runner_on_skipped(self, result):
        self._record(result._task, "skipped")

    def v2_runner_on_unreachable(self, result):
        self._record(result._task, "unreachable")

    def v2_playbook_on_include(self, included_file):
        self._record(included_file._task, "ok")
