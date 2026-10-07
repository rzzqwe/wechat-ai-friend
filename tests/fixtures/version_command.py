import sys
import time
from pathlib import Path

# Emulate a CLI that prints its version before native process cleanup.
print('OpenClaw 2026.9.6 (fixture)', flush=True)
time.sleep(0.2)
Path(sys.argv[1]).write_text('completed')
