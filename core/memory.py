from collections import OrderedDict
from threading import RLock


MAX_MEMORY_ITEMS = 1000
memory = OrderedDict()
_lock = RLock()


def set_context(key, value):
    with _lock:
        memory[key] = value
        memory.move_to_end(key)
        while len(memory) > MAX_MEMORY_ITEMS:
            memory.popitem(last=False)


def get_context(key):
    with _lock:
        value = memory.get(key)
        if key in memory:
            memory.move_to_end(key)
        return value
