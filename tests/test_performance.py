import pytest
import asyncio
from base_engine.data.polymarket_client import PolymarketClient, TokenBucket, CircuitBreaker


@pytest.mark.asyncio
async def test_token_bucket():
    bucket = TokenBucket(rate=10, burst=20)
    
    acquired = await bucket.acquire(5)
    assert acquired is True

    tokens_before = bucket.tokens
    await bucket.acquire(100)
    tokens_after = bucket.tokens
    # Use <= to avoid flaky float comparison (timer can add fractional tokens)
    assert tokens_after <= tokens_before + 0.1


@pytest.mark.asyncio
async def test_circuit_breaker():
    breaker = CircuitBreaker(failure_threshold=3, timeout=1)
    
    async def failing_func():
        raise Exception("Test failure")
    
    for _ in range(3):
        try:
            await breaker.call(failing_func)
        except Exception:
            pass
    
    assert breaker.state == "OPEN"
    
    await asyncio.sleep(1.1)
    
    async def success_func():
        return "success"
    
    result = await breaker.call(success_func)
    assert result == "success"
    assert breaker.state == "CLOSED"


@pytest.mark.asyncio
async def test_polymarket_client_context():
    async with PolymarketClient() as client:
        assert client.client is not None
        assert client.rate_limiter is not None
        assert client.circuit_breaker is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
