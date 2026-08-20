"""Offline compatibility entry point for AT0000605324 reconciliation.

The reconciliation command now reads only the already-downloaded Morningstar
evidence supplied on disk. It never contacts OeKB, Morningstar, or any other
network endpoint.
"""

from importlib import import_module

if __name__ == "__main__":
    reconciliation = import_module("reconcile_at0000605324")
    raise SystemExit(reconciliation.main())
