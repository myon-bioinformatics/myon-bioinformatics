"""Stdlib stand-in for the ``requests`` calls Aoi actually makes.

The used surface is empty. No file under Aoi or the six child subtrees imports
``requests`` (see ``pkgmin/evidence/used-surface.json``). This module therefore
exports no functions.

``urllib.request`` is the client a shrink would have wrapped, and QueRyu's
``UrllibClient`` already is that client under its own name. Nothing here calls
it: shipping ``get`` / ``Session`` / ``post`` would invent an API the tree does
not use, and the rest of requests (cookies, adapters, multipart, streaming,
retries) is not a stdlib subset even if a call showed up later.
"""
from __future__ import annotations

SURFACE: tuple[str, ...] = ()


class UsedSurfaceError(AttributeError):
    """A name was requested that the survey did not find on ``requests``."""


def __getattr__(name: str):
    raise UsedSurfaceError(
        f"requests.{name} is not in the used surface "
        "(Aoi and its child subtrees do not import requests; urllib would back a call that does not exist)"
    )
