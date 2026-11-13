"""Reentrant thread/process lock for the shared JSON result store."""
import os
import threading
import time
from contextlib import contextmanager


@contextmanager
def file_lock(path, blocking=True):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, 'a+b') as handle:
        if os.name == 'nt':
            import msvcrt
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b'0')
                handle.flush()
            handle.seek(0)
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if not blocking:
                        raise
                    time.sleep(0.05)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        try:
            yield
        finally:
            if os.name == 'nt':
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


class ResultsLock:
    def __init__(self, path):
        self.path = path
        self.mutex = threading.RLock()
        self.local = threading.local()

    def __enter__(self):
        self.mutex.acquire()
        try:
            if not getattr(self.local, 'depth', 0):
                self.local.guard = file_lock(self.path())
                self.local.guard.__enter__()
            self.local.depth = getattr(self.local, 'depth', 0) + 1
        except BaseException:
            self.mutex.release()
            raise
        return self

    def __exit__(self, *exc):
        try:
            self.local.depth -= 1
            if not self.local.depth:
                self.local.guard.__exit__(*exc)
        finally:
            self.mutex.release()
