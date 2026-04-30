import queue
import time
from app.core.pixiv_thread_base import PauseableThread


class test_thread(PauseableThread):
    """Legacy test thread for backward compatibility."""

    def __init__(self, *args, **kwargs):
        q = kwargs.pop('q', None) or queue.Queue()
        super().__init__(q=q)
        self._isPause = False
        self._value = 0

    def run(self):
        while not self._stop_event.is_set():
            print(1)
            # Wait for pause event (set means not paused)
            self._pause_event.wait()
            if self._stop_event.is_set():
                break
            time.sleep(1)








