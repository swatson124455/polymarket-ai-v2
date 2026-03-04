"""
GPU Utilities - GPU detection and fallback handling.
Provides transparent numpy/cupy interface for GPU-accelerated operations.
"""
from structlog import get_logger

logger = get_logger()

_GPU_AVAILABLE = None
_ARRAY_MODULE = None


def has_gpu() -> bool:
    """Check if GPU is available with CuPy. Cached after first check."""
    global _GPU_AVAILABLE
    
    if _GPU_AVAILABLE is not None:
        return _GPU_AVAILABLE
    
    try:
        import cupy as cp
        # Test GPU access
        test_array = cp.array([1.0, 2.0, 3.0])
        _ = cp.asnumpy(test_array)
        _GPU_AVAILABLE = True
        logger.info("GPU detected and functional with CuPy")
        return True
    except ImportError:
        logger.info("CuPy not installed - using CPU mode")
        _GPU_AVAILABLE = False
        return False
    except Exception as e:
        logger.warning(f"GPU check failed: {e}. Using CPU mode.")
        _GPU_AVAILABLE = False
        return False


def get_array_module():
    """
    Get array module (cupy or numpy).
    Returns cupy if GPU available, otherwise numpy.
    Use this for transparent GPU/CPU code.
    """
    global _ARRAY_MODULE
    
    if _ARRAY_MODULE is not None:
        return _ARRAY_MODULE
    
    if has_gpu():
        import cupy as cp
        _ARRAY_MODULE = cp
        logger.info("Using CuPy for GPU-accelerated arrays")
    else:
        import numpy as np
        _ARRAY_MODULE = np
        logger.info("Using NumPy for CPU arrays")
    
    return _ARRAY_MODULE


def to_cpu(array):
    """Convert array to CPU (numpy). Handles both cupy and numpy."""
    if has_gpu():
        import cupy as cp
        if isinstance(array, cp.ndarray):
            return cp.asnumpy(array)
    return array


def to_gpu(array):
    """Convert array to GPU (cupy) if available. Otherwise returns as-is."""
    if has_gpu():
        import cupy as cp
        import numpy as np
        if isinstance(array, np.ndarray):
            return cp.asarray(array)
    return array


def clear_gpu_memory():
    """Clear GPU memory pools. Call periodically to prevent memory leaks."""
    if has_gpu():
        try:
            import cupy as cp
            cp.get_default_memory_pool().free_all_blocks()
            logger.debug("GPU memory cleared")
        except Exception as e:
            logger.warning(f"Failed to clear GPU memory: {e}")
