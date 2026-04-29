"""File-backed simulator for the lab communicator.

Public re-export so existing call sites keep working::

    from lab_communicator.mock import MockLabCommunicator

After the Phase 1 folder restructure (see ``communicator_refactor.md``),
the class itself lives in :mod:`lab_communicator.mock.communicator`. This
``__init__`` preserves the import path used by ``backend/main.py`` and
any other caller that imported from the old single-file
``lab_communicator/mock.py``.

The class is **lazy-imported** via PEP 562 (matches the real backend's
pattern) so callers wanting only ``mock.coordinate_frames`` do not pay
the cost of loading the full mock state machine.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lab_communicator.mock.communicator import MockLabCommunicator

__all__ = ["MockLabCommunicator"]


def __getattr__(name: str):
    if name == "MockLabCommunicator":
        from lab_communicator.mock.communicator import MockLabCommunicator
        return MockLabCommunicator
    raise AttributeError(f"module 'lab_communicator.mock' has no attribute {name!r}")
