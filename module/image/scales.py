"""Finite scale grids shared by local and service-side template matching."""
from math import floor, isfinite


MAX_MULTI_SCALE_SAMPLES = 2048


def _sample_count(min_scale: float, max_scale: float, step: float) -> int:
    if not all(isfinite(value) for value in (min_scale, max_scale, step)):
        raise ValueError('Multi-scale bounds and step must be finite')
    if min_scale <= 0 or max_scale <= 0 or step <= 0:
        raise ValueError('Multi-scale bounds and step must be positive')
    intervals = (max_scale - min_scale) / step
    if not isfinite(intervals):
        raise ValueError(f'Multi-scale search exceeds {MAX_MULTI_SCALE_SAMPLES} samples')
    count = floor(intervals + 1e-8) + 1
    if count < 1 or count > MAX_MULTI_SCALE_SAMPLES:
        raise ValueError(f'Multi-scale search exceeds {MAX_MULTI_SCALE_SAMPLES} samples')
    return count


def normalize_multi_scale_range(scale_range, scale_step, *,
                                default_range=(0.6, 1.2), default_step=0.1):
    """Keep the legacy nonpositive-step fallback, reject unbounded grids."""
    if scale_range is None:
        min_scale, max_scale = default_range
        step = default_step
    elif len(scale_range) == 3:
        min_scale, max_scale, step = scale_range
    else:
        min_scale, max_scale = scale_range
        step = scale_step
    min_scale, max_scale, step = float(min_scale), float(max_scale), float(step)
    if not all(isfinite(value) for value in (min_scale, max_scale, step)):
        raise ValueError('Multi-scale bounds and step must be finite')
    if min_scale > max_scale:
        min_scale, max_scale = max_scale, min_scale
    if step <= 0:
        step = float(default_step)
    _sample_count(min_scale, max_scale, step)
    return min_scale, max_scale, step


def multi_scale_values(min_scale: float, max_scale: float, step: float):
    """Index a validated finite grid; floating addition cannot stall the loop."""
    count = _sample_count(min_scale, max_scale, step)
    return tuple(min_scale + index * step for index in range(count))
