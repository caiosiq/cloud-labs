"""Compatibility shim — prefer ``import cloudlabs``.

Install the package::

    pip install -e ./packages/cloudlabs
"""

from cloudlabs import *  # noqa: F403
from cloudlabs import __all__ as __all__  # noqa: F401
