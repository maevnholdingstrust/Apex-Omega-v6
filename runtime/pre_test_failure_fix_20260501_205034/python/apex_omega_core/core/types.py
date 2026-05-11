"""Compatibility exports for legacy ``apex_omega_core.core.types`` imports.

The active Python data model lives in :mod:`apex_omega_core.core.domain_types`.
Keep this shim thin so older dashboard/runtime code can import ``core.types``
without forking the schema.
"""

from .domain_types import *  # noqa: F401,F403
