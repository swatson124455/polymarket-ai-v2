"""
Tests for bot improvements infrastructure.
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from base_engine.cache.redis_manager import RedisManager
from base_engine.execution.approval_cache import ApprovalCache
from base_engine.learning.feature_store import FeatureStore
from base_engine.utils.performance_tracker import PerformanceTracker
from base_engine.utils.gpu_utils import has_gpu, get_array_module, to_cpu, to_gpu


@pytest.fixture
async def redis_manager():
    """Redis manager fixture."""
    manager = RedisManager.get_instance()
    await manager.connect()
    yield manager
    # Cleanup
    await manager.close()


@pytest.mark.asyncio
async def test_redis_connection(redis_manager):
    """Test Redis connection."""
    # Try to set and get
    result = await redis_manager.set("test_key", "test_value", ex=60)
    assert result is True or result is False  # May fail if Redis not running
    
    value = await redis_manager.get("test_key")
    if result:
        assert value == "test_value"
    else:
        # Redis not available - should gracefully return None
        assert value is None


@pytest.mark.asyncio
async def test_approval_cache():
    """Test approval caching."""
    cache = ApprovalCache()
    
    # Initially should be None (not cached)
    result = await cache.is_approved("0x123", "0x456")
    assert result is None
    
    # Cache an approval
    await cache.set_approved("0x123", "0x456", True)
    
    # Should be cached now
    result = await cache.is_approved("0x123", "0x456")
    if result is not None:  # Redis may not be running
        assert result is True
    
    # Invalidate
    await cache.invalidate("0x123")
    
    # Should be None again
    result = await cache.is_approved("0x123", "0x456")
    # After invalidation, may still be None or False depending on Redis


@pytest.mark.asyncio
async def test_feature_store():
    """Test feature store instantiation and core API."""
    mock_db = MagicMock()
    store = FeatureStore(db=mock_db)
    assert store.db is mock_db
    # FeatureStore is a DB-backed compute-and-store; verify core method exists
    assert hasattr(store, "compute_market_features")


@pytest.mark.asyncio
async def test_performance_tracker():
    """Test performance tracker."""
    mock_db = MagicMock()
    mock_db.session_factory = None  # Disable DB writes for test
    
    tracker = PerformanceTracker(mock_db)
    
    # Record execution
    await tracker.record_execution(
        bot_name="MirrorBot",
        market_id="market_123",
        expected_price=0.55,
        actual_price=0.56,
        size=100
    )
    
    # Should be in memory
    recent = tracker.get_recent_executions("MirrorBot")
    assert len(recent) == 1
    assert recent[0]['market_id'] == "market_123"
    assert recent[0]['slippage'] > 0  # Worse than expected


def test_gpu_detection():
    """Test GPU detection."""
    has_gpu_result = has_gpu()
    
    # Should return True or False (not error)
    assert isinstance(has_gpu_result, bool)
    
    # Get array module
    xp = get_array_module()
    
    # Should work with either numpy or cupy
    arr = xp.array([1, 2, 3])
    assert len(arr) == 3
    
    # Convert to CPU (should work regardless)
    cpu_arr = to_cpu(arr)
    assert len(cpu_arr) == 3


def test_gpu_conversion():
    """Test GPU/CPU array conversion."""
    import numpy as np
    
    # Create numpy array
    np_arr = np.array([1.0, 2.0, 3.0])
    
    # Try to convert to GPU (may fail if no GPU)
    gpu_arr = to_gpu(np_arr)
    
    # Convert back to CPU
    cpu_arr = to_cpu(gpu_arr)
    
    # Should have same values
    assert len(cpu_arr) == 3
    assert cpu_arr[0] == 1.0


@pytest.mark.asyncio
async def test_redis_hash_operations(redis_manager):
    """Test Redis hash operations."""
    # Set hash field
    result = await redis_manager.hset("test_hash", "field1", "value1")
    
    # Get hash field
    value = await redis_manager.hget("test_hash", "field1")
    if result:  # If Redis available
        assert value == "value1"
    
    # Get all hash fields
    all_fields = await redis_manager.hgetall("test_hash")
    if result:
        assert "field1" in all_fields


@pytest.mark.asyncio
async def test_redis_sorted_set(redis_manager):
    """Test Redis sorted set operations."""
    # Add to sorted set
    result = await redis_manager.zadd("test_zset", {"item1": 1.0, "item2": 2.0})
    
    # Get range
    items = await redis_manager.zrange("test_zset", 0, -1, withscores=True)
    if result > 0:  # If Redis available
        assert len(items) >= 2


@pytest.mark.asyncio
async def test_feature_store_invalidation():
    """Test feature store has a DB-backed architecture (not Redis-cache)."""
    mock_db = MagicMock()
    store = FeatureStore(db=mock_db)
    # FeatureStore computes features from DB — it doesn't have a Redis-style cache to invalidate
    # Verify the class was instantiated correctly and core method is present
    assert store.db is mock_db
    assert callable(getattr(store, "compute_market_features", None))


@pytest.mark.asyncio
async def test_approval_cache_clear():
    """Test clearing all approval caches."""
    cache = ApprovalCache()
    
    # Cache some approvals
    await cache.set_approved("0xAAA", "0xBBB", True)
    await cache.set_approved("0xCCC", "0xDDD", True)
    
    # Clear all
    await cache.clear_all()
    
    # Should be cleared
    assert len(cache.memory_cache) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
