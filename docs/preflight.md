# Repository prerequisites

The parent owns this small connection between its already-adopted `gh_identity`
readers and optional JSON from `yourself.collect()`. It uses the Python standard
library and does not modify vendor enrollment or pins.

```sh
python -S /path/to/yourself.py --format json > /tmp/runtime-facts.json
python -S preflight.py --root /path/to/checkout --require git --python-min 3.11 --runtime-facts /tmp/runtime-facts.json
python -S preflight.py --root /path/to/checkout --require git --require gh --require-clean --require-token
```

It reports ghi capabilities and bounded Git status counts, without file paths or
identities. It only runs ghi's existing fixed version/status probes; no GitHub
API requests, installation, remote fetch, credential printing, or supplied code
execution. Token presence does not prove authentication, permission, or GitHub
availability. A truncated status cannot satisfy `--require-clean`.

The optional yourself JSON must use `collect()` schema version 1, not a runner
fingerprint or a diagnose report. Only OS/runtime fields survive; host and
directory facts are discarded. Reads are bounded to 64 KiB. Supplied facts are
labelled `freshness: not_verified` and do not decide the current Python check.
Run yourself again to compare local and CI environments; the tool does not claim
that a receipt belongs to the current checkout or runtime.

Exit 0 means the explicitly requested checks passed (an empty check list is not
an application readiness claim); exit 1 means a requested prerequisite failed;
exit 2 means invalid input or an unavailable observation, including a non-Git
root. No application or pipeline step is run.

For NagoyAction, run its existing project-specific doctor after these environment
observations, rather than inferring readiness from generic capabilities:

```sh
python -S /path/to/NagoyAction/nagoyaction.py doctor /path/to/pipeline.toml --offline
```

The c001 Aoi example correctly reported missing cached fetch manifests in this
checkout (exit 1) despite usable Python and Git. Missing optional `gh` was a
warning. This is evidence of a blocked offline run, not a completed pipeline.
