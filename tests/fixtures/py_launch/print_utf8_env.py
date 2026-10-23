#!/usr/bin/env python3
import json
import os

print(
    json.dumps(
        {
            "PYTHONUTF8": os.environ.get("PYTHONUTF8"),
            "PYTHONIOENCODING": os.environ.get("PYTHONIOENCODING"),
        }
    )
)
