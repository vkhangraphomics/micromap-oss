"""Registry mapping a config normalizer-name to a normalizer callable.

`x_mapforge.normalizer` is a string in the schema_config; the resolver registry
dispatches it to a function here instead of hardcoding the import. Seeded with
the one normalizer that exists today; extend `_NORMALIZERS` to add more.
"""

import logging
from typing import Callable

from ..normalize import normalize_disease_name

logger = logging.getLogger(__name__)

_NORMALIZERS: dict[str, Callable[[str], str]] = {
    "normalize_disease_name": normalize_disease_name,
    "disease": normalize_disease_name,  # short alias
}


def get_normalizer(name: str | None) -> Callable[[str], str] | None:
    """Return the normalizer callable for `name`, or None.

    None/empty -> None (no normalization). An unknown name -> None plus a
    warning, so a typo'd normalizer is visible but never fatal.
    """
    if not name:
        return None
    fn = _NORMALIZERS.get(name)
    if fn is None:
        logger.warning("unknown normalizer %r; falling back to no normalization", name)
    return fn
