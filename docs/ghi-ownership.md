# Git/GitHub implementation ownership

The Git read implementation moves to gh_identity PR #23. This parent consumer
explicitly pins the tested transfer commit; the recommended catalog pin remains
independent and is not silently promoted.

`git_inspector.py` now contains only a compatibility adapter over the locked GHI
copy. Old public names, arguments and result shapes remain, and there is no source
download during import. Existing consumers holding older standalone copies are
not silently updated. New installations can use GHI's `git_*` APIs directly.

Ordinary gh_ops PR status reads now use GHI's bounded transport. Default owner/org
open-PR search uses GHI's canonical search and exposes incomplete/truncated state.
Injected clients and selected-repository adapters remain compatible. Check
classification and SHA comparison already delegate to GHI. gh_ops keeps the
operational presentation, polling and explicit mutation workflow.

This is an initial consolidation, not a claim that every gh_ops reader has been
migrated. Comment export/full-body digest and selected-repo/count paths still have
legacy adapters; their output contracts need separate compatibility work.
