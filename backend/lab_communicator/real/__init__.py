"""Cloud-labs / lab_automation backend for the lab communicator.

Public re-export so existing call sites keep working::

    from lab_communicator.real import RealLabCommunicator

After the Phase 1 folder restructure (see ``communicator_refactor.md``),
the class itself lives in :mod:`lab_communicator.real.communicator`. This
``__init__`` preserves the import path used by ``backend/main.py`` and
any other caller that imported from the old single-file
``lab_communicator/real.py``.

The class is **lazy-imported** via PEP 562 so that callers wanting only
``real.coordinate_frames`` (or any other sibling module) do not also
load the heavy ``communicator`` module -- which pulls in
``lab_automation``, OpenCV, recorder helpers, etc. ``RealLabCommunicator``
materializes only when explicitly accessed.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Type-checker / IDE sees the symbol; runtime does not pay the
    # import cost until __getattr__ fires below.
    from lab_communicator.real.communicator import RealLabCommunicator

__all__ = ["RealLabCommunicator"]


def __getattr__(name: str):
    # PEP 562 module-level __getattr__: invoked only when ``name`` is
    # not already a module attribute. The first attribute access does
    # the real import; subsequent accesses hit the cached module.
    if name == "RealLabCommunicator":
        from lab_communicator.real.communicator import RealLabCommunicator
        return RealLabCommunicator
    raise AttributeError(f"module 'lab_communicator.real' has no attribute {name!r}")
