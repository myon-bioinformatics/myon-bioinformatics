"""Stdlib stand-in for the ``tqdm`` calls Aoi actually makes.

The used surface is empty. No file imports ``tqdm``. A shrink of a progress
wrapper is an iterator that yields the same items and draws nothing:

    def tqdm(iterable, **_unused):
        yield from iterable

That function is not defined here. There is no loop wrapped in ``tqdm``, so
there is no project output to compare against the real bar, and a display
(``dynamic_ncols``, nested bars, notebook widgets, ``pandas``) is not part of
any result this tree computes.
"""
from __future__ import annotations

SURFACE: tuple[str, ...] = ()


class UsedSurfaceError(AttributeError):
    """A name was requested that the survey did not find on ``tqdm``."""


def __getattr__(name: str):
    raise UsedSurfaceError(
        f"tqdm.{name} is not in the used surface "
        "(Aoi and its child subtrees do not import tqdm; a progress iterator would wrap a loop that does not exist)"
    )
