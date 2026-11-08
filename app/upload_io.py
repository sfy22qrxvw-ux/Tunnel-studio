# Copyright (c) 2026 Damian Gulczynski.
"""Bounded upload copying with cleanup of incomplete transfer files."""
from pathlib import Path


def receive(source, destination, length):
    destination = Path(destination)
    if not isinstance(length, int) or length < 0:
        raise ValueError('Invalid upload size.')
    try:
        with destination.open('wb') as output:
            remaining = length
            while remaining:
                block = source.read(min(1024 * 1024, remaining))
                if not block:
                    raise ValueError('Incomplete upload. Please choose the file again.')
                output.write(block)
                remaining -= len(block)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
