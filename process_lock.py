import os
from pathlib import Path
from config import LOCK_FILE

class ProcessLockError(RuntimeError):
    pass

class ProcessLock:
    def __init__(self, path=LOCK_FILE):
        self.path = Path(path)
        self.acquired = False

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(str(os.getpid()))
                f.flush()
                os.fsync(f.fileno())
        except FileExistsError as exc:
            raise ProcessLockError(f"lock already exists: {self.path}") from exc
        self.acquired = True

    def release(self):
        if not self.acquired:
            return
        try:
            self.path.unlink(missing_ok=True)
        finally:
            self.acquired = False
