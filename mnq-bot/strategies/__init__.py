"""Strategy package (curated public subset).

Only the base class is re-exported here so the package imports cleanly with
the example strategies that ship in this repo. Import individual strategies
by module path, e.g. `from strategies.ema_rsi_strategy import EmaRsiStrategy`.
"""

from .base_strategy import BaseStrategy

__all__ = ["BaseStrategy"]
