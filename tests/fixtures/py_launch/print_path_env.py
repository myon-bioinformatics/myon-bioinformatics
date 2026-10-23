#!/usr/bin/env python3
import json
import os

print(
    json.dumps(
        {
            "has_path": "PATH" in os.environ,
            "has_systemroot": "SYSTEMROOT" in os.environ,
            "marker": os.environ.get("PY_LAUNCH_ENV_MARKER"),
        }
    )
)
