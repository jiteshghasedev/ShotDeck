"""Running something off the UI thread, once, correctly.

Every background call in Flow goes through here. The one subtlety worth
keeping in one place: QThreadPool takes ownership of the C++ runnable, but
nothing holds the Python wrapper, and a collected wrapper takes its signals
with it -- the callbacks then never fire and the UI waits forever. `run()`
keeps the worker in a set until it finishes.
"""

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

import applog

log = applog.get()


class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(str)


class Worker(QRunnable):
    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.signals = WorkerSignals()

    def run(self):
        try:
            self.signals.result.emit(self.fn(*self.args, **self.kwargs))
        except Exception as e:
            # Callers only get str(e); keep the traceback for `main.py -v`.
            log.debug("%s failed", getattr(self.fn, "__qualname__", self.fn),
                      exc_info=True)
            self.signals.error.emit(str(e))


def run(keep, fn, on_result, *args, on_error=None, pool=None, **kwargs):
    """Run `fn(*args)` on the pool and deliver the result on the UI thread.

    `keep` is the caller's set of in-flight workers; the worker removes itself
    from it when it is done.
    """
    worker = Worker(fn, *args, **kwargs)
    keep.add(worker)

    def finished(*_):
        keep.discard(worker)

    worker.signals.result.connect(on_result)
    worker.signals.result.connect(finished)
    if on_error:
        worker.signals.error.connect(on_error)
    worker.signals.error.connect(finished)
    (pool or QThreadPool.globalInstance()).start(worker)
    return worker
