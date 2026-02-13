"""Anti-detection jitter utilities for order timing and sizing."""

from __future__ import annotations

import random


def jitter_size(size: float, pct: float) -> float:
    """Add +/-pct random noise to order size.

    Args:
        size: Original order size.
        pct: Jitter percentage as a decimal (e.g. 0.10 for 10%).

    Returns:
        Size with random noise applied.  Always >= 0.
    """
    if pct <= 0:
        return size
    return max(0.0, size * (1.0 + random.uniform(-pct, pct)))


def jitter_delay(base_seconds: float, pct: float) -> float:
    """Add +/-pct random noise to a sleep interval.

    Args:
        base_seconds: Original interval in seconds.
        pct: Jitter percentage as a decimal (e.g. 0.15 for 15%).

    Returns:
        Interval with random noise applied.  Always >= 0.
    """
    if pct <= 0:
        return base_seconds
    return max(0.0, base_seconds * (1.0 + random.uniform(-pct, pct)))
