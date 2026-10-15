"""Courtesy window for long polite runs: work only between HH:MM-HH:MM.

Same semantics as the harvester's --schedule (a window may cross
midnight; the end is exclusive), kept free of its logging and exceptions
so any script can gate its requests with it.
"""

import datetime
import time

CHECK_SECONDS = 300
TICK_SECONDS = 5


def parse_schedule(text):
    """'23:00-06:45' -> (time, time); empty -> None (no window)."""
    if not text:
        return None
    start, end = text.split('-')
    return (datetime.datetime.strptime(start.strip(), '%H:%M').time(),
            datetime.datetime.strptime(end.strip(), '%H:%M').time())


def in_schedule(window, now=None):
    if window is None:
        return True
    now = now or datetime.datetime.now().time()
    start, end = window
    if start <= end:
        return start <= now < end
    return now >= start or now < end


def wait_for_schedule(window, should_stop=lambda: False, on_pause=None,
                      sleep=None, check=CHECK_SECONDS,
                      tick=TICK_SECONDS):
    """Block until the window is open. -> True if it waited at all.

    The window is looked at every `check` seconds, a stop request every
    `tick`: a plain sleep(check) would make SIGTERM take up to five
    minutes to land. Returns early, window still closed, once
    `should_stop()` turns true; the caller decides what stopping means."""
    if in_schedule(window):
        return False
    # Looked up at call time so a patched time.sleep (tests) is honored.
    sleep = sleep or time.sleep
    if on_pause:
        on_pause()
    while not in_schedule(window):
        waited = 0
        while waited < check:
            if should_stop():
                return True
            sleep(tick)
            waited += tick
    return True
