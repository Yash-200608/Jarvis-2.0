#!/data/data/com.termux/files/usr/bin/bash
# Probe — emits the PID(s) of phone_executor.py to stdout, or nothing.
# Tasker reads %stdout to decide whether to relaunch.
pgrep -f "python.*phone_executor.py" || true
