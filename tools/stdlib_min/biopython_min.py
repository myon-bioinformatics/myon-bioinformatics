"""Stdlib stand-in for the BioPython calls Aoi actually makes.

The distribution name is ``biopython``. The import name is ``Bio``. Neither
appears in Aoi or the six child subtrees, so no parser or sequence function is
on the used surface. This module exports nothing.

A used ``Bio.SeqIO`` / ``Align`` / ``SearchIO`` call would be the thing to
reimplement. There is no such call, and several BioPython submodules are C
extensions (``Bio.Align`` aligners, pairwise code, Nexus). A stdlib FASTA
reader would be a new parser, not a shrink of a call this tree makes.
"""
from __future__ import annotations

# Import name is Bio, not biopython. The runner loads this file as ``Bio``.
SURFACE: tuple[str, ...] = ()


class UsedSurfaceError(AttributeError):
    """A name was requested that the survey did not find on ``Bio``."""


def __getattr__(name: str):
    raise UsedSurfaceError(
        f"Bio.{name} is not in the used surface "
        "(Aoi and its child subtrees do not import Bio or biopython)"
    )
