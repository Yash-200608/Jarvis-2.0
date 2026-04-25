memory = {}


def set_context(key, value):
    memory[key] = value


def get_context(key):
    return memory.get(key)
