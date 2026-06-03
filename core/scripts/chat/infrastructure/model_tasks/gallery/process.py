"""Run gallery_describe_subprocess.py via QProcess (no GUI LLM unload)."""

from __future__ import annotations

import json
import os

from PyQt6.QtCore import QObject, QProcess, QProcessEnvironment, pyqtSignal

from infrastructure.model_tasks.paths import (
    gallery_describe_subprocess_script,
    lira_project_root,
    python_executable,
)


class GalleryDescribeProcess(QObject):
    """Read NDJSON from child stdout."""

    json_line = pyqtSignal(dict)
    failed = pyqtSignal(str)
    finished_ok = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._proc: QProcess | None = None
        self._buf = ""
        self._failed_emitted = False

    @staticmethod
    def project_root():
        return lira_project_root()

    @staticmethod
    def script_path():
        return gallery_describe_subprocess_script()

    @staticmethod
    def python_executable() -> str:
        return python_executable()

    @staticmethod
    def process_environment() -> QProcessEnvironment:
        root = lira_project_root()
        env = QProcessEnvironment.systemEnvironment()
        py_path = env.value("PYTHONPATH") or ""
        parts = [str(root), str(root / "core" / "scripts" / "chat")]
        if py_path:
            parts.append(py_path)
        env.insert("PYTHONPATH", os.pathsep.join(parts))
        return env

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.state() != QProcess.ProcessState.NotRunning

    def start(self, job_path: str) -> None:
        script = self.script_path()
        if not script.is_file():
            self.failed.emit(f"Script not found: {script}")
            return

        self._buf = ""
        self._failed_emitted = False
        self._proc = QProcess(self)
        env = self.process_environment()
        env.insert("CUDA_MODULE_LOADING", "LAZY")
        self._proc.setProcessEnvironment(env)
        self._proc.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self._proc.readyReadStandardOutput.connect(self._on_stdout)
        self._proc.readyReadStandardError.connect(self._on_stderr)
        self._proc.finished.connect(self._on_finished)
        self._proc.errorOccurred.connect(self._on_process_error)
        self._proc.start(self.python_executable(), [str(script), job_path])

    def cancel(self) -> None:
        if self._proc is None:
            return
        if self._proc.state() != QProcess.ProcessState.NotRunning:
            self._proc.kill()

    @staticmethod
    def _is_json_line(line: str) -> bool:
        s = line.strip()
        return bool(s) and s.startswith("{")

    def _emit_parsed_lines(self) -> None:
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.strip()
            if not line or not self._is_json_line(line):
                continue
            try:
                self.json_line.emit(json.loads(line))
            except json.JSONDecodeError:
                print(f"[GALLERY] subprocess: bad json: {line[:120]!r}", flush=True)

    def _drain_stdout(self) -> None:
        if self._proc is None:
            return
        raw = bytes(self._proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        if raw:
            self._buf += raw
        self._emit_parsed_lines()
        tail = self._buf.strip()
        if tail and self._is_json_line(tail):
            try:
                self.json_line.emit(json.loads(tail))
            except json.JSONDecodeError:
                print(f"[GALLERY] subprocess: bad json tail: {tail[:120]!r}", flush=True)
        self._buf = ""

    def _on_stdout(self) -> None:
        if self._proc is None:
            return
        raw = bytes(self._proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        self._buf += raw
        self._emit_parsed_lines()

    def _on_stderr(self) -> None:
        if self._proc is None:
            return
        err = bytes(self._proc.readAllStandardError()).decode("utf-8", errors="replace").strip()
        if err:
            print(f"[GALLERY subprocess stderr] {err[-2000:]}", flush=True)

    def _fail(self, message: str) -> None:
        if self._failed_emitted:
            return
        self._failed_emitted = True
        self.failed.emit(message)

    def _on_process_error(self, err: QProcess.ProcessError) -> None:
        if err == QProcess.ProcessError.Crashed:
            self._fail("Child process failed (CUDA/RAM).")
        elif err != QProcess.ProcessError.FinishedNormally:
            self._fail(f"Could not start description process: {err.name}")

    def _on_finished(self, exit_code: int, _status) -> None:
        self._drain_stdout()
        if exit_code != 0:
            self._fail(f"Description process exited with code {exit_code}.")
        else:
            self.finished_ok.emit()
