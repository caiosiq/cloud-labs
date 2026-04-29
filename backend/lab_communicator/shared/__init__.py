"""Cross-lab building blocks for the lab communicator.

Modules in this package are usable by *any* communicator backend (real,
mock, or a hypothetical future Robot B). Architectural rules enforced
by convention and a CI lint (see ``communicator_refactor.md`` §5.1):

- ``shared/`` never imports from ``lab_communicator.real``,
  ``lab_communicator.mock``, or ``lab_automation``.
- ``shared/`` never imports ``lab_communicator.base`` (would create a
  circular import; see Trap 3 in the refactor doc).
- Helpers that need to mutate communicator state accept the specific
  primitives they touch (``current_state: dict``,
  ``lock: threading.Lock``, ``catalog_map: dict``) -- never the
  ``LabCommunicator`` instance.
"""
