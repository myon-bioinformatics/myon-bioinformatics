"""Fixture for py_launch call(): functions, a class, and a dataclass, with a __main__ guard that must not run."""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import check_nosite as sibling  # noqa: F401  (sibling import must work: script dir is on sys.path; its print lands on stderr)


@dataclass
class Point:
    x: int
    y: int
    label: str = "p"


class Counter:
    def __init__(self, start=0):
        self.start = start

    def add(self, n, *, times=1):
        return self.start + n * times

    @staticmethod
    def version():
        return "1.0"


def add(a, b=1):
    return a + b


def greet(name):
    print(f"hello {name}")  # must go to stderr, not into the JSON result
    return {"name": name, "module": __name__, "main": __name__ == "__main__"}


def mixed():
    return {"set": {1}, "tuple": (1, 2), "path": Path("a/b"), "point": Point(1, 2), "text": "日本語"}


def boom():
    raise ValueError("boom!")


if __name__ == "__main__":
    print("GUARD RAN")
    sys.exit(9)
