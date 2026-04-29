"""Lab communicator package.

Public surface (preserved across the Phase 1 folder restructure --
see ``communicator_refactor.md`` for the full design).

Imports::

    from lab_communicator.base import LabCommunicator
    from lab_communicator.real import RealLabCommunicator
    from lab_communicator.mock import MockLabCommunicator

The top-level package itself does NOT re-export the concrete backends.
That is intentional: importing :mod:`lab_communicator` should not pull
in either backend's implementation (``real`` drags in ``lab_automation``,
``mock`` reads from the mock state JSON). Callers ask for the backend
they need by its fully qualified path. Only :class:`LabCommunicator` is
cheap to import unconditionally and that path is unchanged.
"""
