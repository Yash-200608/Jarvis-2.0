class ExecutionState:
    CREATED = "created"
    VALIDATED = "validated"
    EXECUTING = "executing"
    SUCCESS = "success"
    FAILED = "failed"


VALID_TRANSITIONS = {
    ExecutionState.CREATED: {ExecutionState.VALIDATED, ExecutionState.FAILED},
    ExecutionState.VALIDATED: {ExecutionState.EXECUTING, ExecutionState.FAILED},
    ExecutionState.EXECUTING: {ExecutionState.SUCCESS, ExecutionState.FAILED},
    ExecutionState.SUCCESS: set(),
    ExecutionState.FAILED: set(),
}


class TaskState:
    def __init__(self):
        self.state = ExecutionState.CREATED

    def update(self, new_state: str):
        if new_state not in VALID_TRANSITIONS.get(self.state, set()):
            raise ValueError(f"Invalid state transition: {self.state} -> {new_state}")
        self.state = new_state
