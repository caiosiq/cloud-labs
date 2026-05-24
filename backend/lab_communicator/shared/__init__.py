"""Cross-lab building blocks for the lab communicator (paths, factory, file I/O).

Modules here are usable by any communicator backend (real, mock, or future).
Architectural rules:

- ``shared/`` never imports from ``lab_communicator.real``,
  ``lab_communicator.mock``, or ``lab_automation``.
- ``shared/`` never imports ``lab_communicator.base`` (circular import).
- Lab semantics (commits, catalog schema, state machine) live in
  :mod:`lab_model` — import from there, not deprecated shims.

This package owns:

- ``lab_view_config`` — ``LAB_VIEW_PATH`` bootstrap, manifest, paths
- ``communicator_factory`` — mock/real communicator selection
- ``session_checkpoint`` — session persistence
- ``storage_intent`` — stored-component intent file
- ``util`` — small env/parsing helpers
"""
