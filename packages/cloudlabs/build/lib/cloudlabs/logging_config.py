"""Session logging for Cloud Labs SDK scripts."""
from __future__ import annotations

import logging
from typing import Optional, Union

CLOUDLABS_LOGGER = "cloudlabs"


def configure_logging(
    level: Union[int, str] = logging.INFO,
    *,
    format: str = "%(asctime)s %(levelname)s %(message)s",
) -> logging.Logger:
    """One-liner setup for scripts — SDK progress logs under ``cloudlabs``.

    Authors should not sprinkle ``_LOG.info`` around primitives; call this once
    and pass ``verbose=True`` to :func:`connect` (the default).
    """
    root_level = logging.getLevelName(level) if isinstance(level, str) else level
    logging.basicConfig(level=root_level, format=format, force=True)
    log = logging.getLogger(CLOUDLABS_LOGGER)
    log.setLevel(root_level)
    return log


def get_logger() -> logging.Logger:
    return logging.getLogger(CLOUDLABS_LOGGER)
