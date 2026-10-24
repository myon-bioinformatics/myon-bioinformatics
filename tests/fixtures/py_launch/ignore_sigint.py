"""Signal-forwarding fixture: acknowledge SIGINT, then wait for SIGTERM."""
import signal
import time


def ignore_interrupt(_signal, _frame):
    print("IGNORED SIGINT", flush=True)


signal.signal(signal.SIGINT, ignore_interrupt)
print("READY", flush=True)
while True:
    time.sleep(60)
