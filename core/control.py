import threading
import time

class Stopped(Exception):
    pass

class NeedHuman(Exception):
    pass

class Control:
    def __init__(self):
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.generation = 0

    def reset(self):
        self.stop_event.clear()
        self.pause_event.clear()
        self.generation += 1

    def pause(self):
        self.pause_event.set()
        self.generation += 1

    def resume(self):
        self.pause_event.clear()
        self.generation += 1

    def stop(self):
        self.stop_event.set()
        self.pause_event.clear()
        self.generation += 1

    def checkpoint(self, pump=None):
        while self.pause_event.is_set() and not self.stop_event.is_set():
            self._tick(.15, pump)
        if self.stop_event.is_set():
            raise Stopped('任务已停止')

    @staticmethod
    def _tick(seconds, pump):
        if pump:
            pump(max(1, int(seconds * 1000)))
        else:
            time.sleep(seconds)

    def wait(self, seconds, pump=None):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.checkpoint(pump)
            self._tick(min(.2, max(.001, deadline - time.monotonic())), pump)
        self.checkpoint(pump)
