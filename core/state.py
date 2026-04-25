class ExecutionState:
    CREATED = "created"
    VALIDATED = "validated"
    EXECUTING = "executing"
    SUCCESS = "success"
    FAILED = "failed"


class TaskState:
    def __init__(self):
        self.state = ExecutionState.CREATED

    def update(self, new_state: str):
        self.state = new_state
