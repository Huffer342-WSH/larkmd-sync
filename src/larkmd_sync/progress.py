"""Optional live diagnostics; API calls without a callback stay quiet."""

from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Event, Thread
from time import monotonic

sink: ContextVar[Callable[[str], None] | None] = ContextVar('progress_sink', default=None)


def report(message: str) -> None:
    callback = sink.get()
    if callback is not None:
        callback(f'[larkmd-sync] {message}')


@contextmanager
def waiting(label: str):
    callback = sink.get()
    if callback is None:
        yield
        return
    started = monotonic()
    stopped = Event()

    def heartbeat():
        while not stopped.wait(10):
            callback(f'[larkmd-sync] 等待 {label}，已耗时 {monotonic() - started:.0f}s')

    report(f'开始 {label}')
    worker = Thread(target=heartbeat, daemon=True)
    worker.start()
    try:
        yield
    finally:
        stopped.set()
        worker.join()
        report(f'结束 {label}，耗时 {monotonic() - started:.1f}s')
