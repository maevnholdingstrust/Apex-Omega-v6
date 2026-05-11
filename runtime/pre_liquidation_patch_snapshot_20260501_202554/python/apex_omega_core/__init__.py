"""
Apex-Omega-v6: High-Performance Polygon Arbitrage System
"""

from .core import *
from .strategies import *
from .operations import *

__version__ = "0.1.0"
from apex_omega_core.runtime import runtime_hooks  # auto-wired providers
