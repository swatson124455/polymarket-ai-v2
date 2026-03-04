import asyncio
from typing import Callable, Optional, Any, Dict, List
from enum import Enum
from datetime import datetime, timezone
from structlog import get_logger

logger = get_logger()


class RecoveryLevel(Enum):
    IMMEDIATE_RETRY = 1
    ALTERNATIVE_SOURCE = 2
    RECONSTRUCT = 3
    FETCH_HISTORICAL = 4
    ALERT_AND_WAIT = 5
    DEGRADE = 6


class RecoveryHierarchy:
    def __init__(
        self,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        alternative_sources: Optional[List[Callable]] = None,
        reconstruct_fn: Optional[Callable] = None,
        historical_fn: Optional[Callable] = None,
        max_state_size: int = 10000
    ):
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.alternative_sources = alternative_sources or []
        self.reconstruct_fn = reconstruct_fn
        self.historical_fn = historical_fn
        self.recovery_state: Dict[str, Dict] = {}
        self.max_state_size = max_state_size
    
    async def execute_with_recovery(
        self,
        operation_name: str,
        primary_operation: Callable,
        is_critical: bool = True,
        validation_fn: Optional[Callable] = None
    ) -> Any:
        recovery_level = RecoveryLevel.IMMEDIATE_RETRY
        last_error = None
        
        while recovery_level.value <= RecoveryLevel.DEGRADE.value:
            try:
                result = await self._try_recovery_level(
                    recovery_level,
                    operation_name,
                    primary_operation,
                    validation_fn
                )
                
                if result is not None:
                    self._record_success(operation_name, recovery_level)
                    return result
                
                # Check if next recovery level exists before incrementing
                next_level_value = recovery_level.value + 1
                if next_level_value <= RecoveryLevel.DEGRADE.value:
                    recovery_level = RecoveryLevel(next_level_value)
                else:
                    break
                
            except Exception as e:
                last_error = e
                logger.warning(
                    f"Recovery level {recovery_level.name} failed for {operation_name}",
                    error=str(e),
                    recovery_level=recovery_level.name
                )
                
                # Check if next recovery level exists before incrementing
                next_level_value = recovery_level.value + 1
                if next_level_value <= RecoveryLevel.DEGRADE.value:
                    recovery_level = RecoveryLevel(next_level_value)
                else:
                    break
        
        if is_critical:
            error_msg = f"All recovery attempts failed for {operation_name}"
            if last_error:
                error_msg += f": {str(last_error)}"
            raise RuntimeError(error_msg)
        
        logger.warning(f"Non-critical operation {operation_name} failed after all recovery attempts")
        return None
    
    async def _try_recovery_level(
        self,
        level: RecoveryLevel,
        operation_name: str,
        primary_operation: Callable,
        validation_fn: Optional[Callable]
    ) -> Optional[Any]:
        if level == RecoveryLevel.IMMEDIATE_RETRY:
            return await self._immediate_retry(primary_operation, validation_fn)
        
        elif level == RecoveryLevel.ALTERNATIVE_SOURCE:
            return await self._try_alternative_sources(operation_name, validation_fn)
        
        elif level == RecoveryLevel.RECONSTRUCT:
            return await self._try_reconstruct(operation_name, validation_fn)
        
        elif level == RecoveryLevel.FETCH_HISTORICAL:
            return await self._try_fetch_historical(operation_name, validation_fn)
        
        elif level == RecoveryLevel.ALERT_AND_WAIT:
            await self._alert_and_wait(operation_name)
            return await self._immediate_retry(primary_operation, validation_fn)
        
        elif level == RecoveryLevel.DEGRADE:
            logger.error(f"Degrading operation {operation_name} - last resort")
            return None
        
        return None
    
    async def _immediate_retry(
        self,
        operation: Callable,
        validation_fn: Optional[Callable]
    ) -> Optional[Any]:
        for attempt in range(self.max_retries):
            try:
                result = await operation()
                
                if validation_fn:
                    if not validation_fn(result):
                        logger.warning(f"Validation failed on attempt {attempt + 1}")
                        if attempt < self.max_retries - 1:
                            await asyncio.sleep(self.retry_delay * (attempt + 1))
                            continue
                        return None
                
                return result
                
            except Exception as e:
                if attempt < self.max_retries - 1:
                    wait_time = self.retry_delay * (2 ** attempt)
                    logger.info(f"Retry attempt {attempt + 1}/{self.max_retries} after {wait_time}s")
                    await asyncio.sleep(wait_time)
                else:
                    raise
        
        return None
    
    async def _try_alternative_sources(
        self,
        operation_name: str,
        validation_fn: Optional[Callable]
    ) -> Optional[Any]:
        if not self.alternative_sources:
            return None
        
        logger.info(f"Trying alternative sources for {operation_name}")
        
        for alt_source in self.alternative_sources:
            try:
                result = await alt_source()
                
                if validation_fn:
                    if not validation_fn(result):
                        continue
                
                logger.info(f"Alternative source succeeded for {operation_name}")
                return result
                
            except Exception as e:
                logger.warning(f"Alternative source failed: {str(e)}")
                continue
        
        return None
    
    async def _try_reconstruct(
        self,
        operation_name: str,
        validation_fn: Optional[Callable]
    ) -> Optional[Any]:
        if not self.reconstruct_fn:
            return None
        
        logger.info(f"Attempting to reconstruct data for {operation_name}")
        
        try:
            result = await self.reconstruct_fn()
            
            if validation_fn:
                if not validation_fn(result):
                    return None
            
            logger.info(f"Reconstruction succeeded for {operation_name}")
            return result
            
        except Exception as e:
            logger.warning(f"Reconstruction failed: {str(e)}")
            return None
    
    async def _try_fetch_historical(
        self,
        operation_name: str,
        validation_fn: Optional[Callable]
    ) -> Optional[Any]:
        if not self.historical_fn:
            return None
        
        logger.info(f"Fetching historical data for {operation_name}")
        
        try:
            result = await self.historical_fn()
            
            if validation_fn:
                if not validation_fn(result):
                    return None
            
            logger.info(f"Historical fetch succeeded for {operation_name}")
            return result
            
        except Exception as e:
            logger.warning(f"Historical fetch failed: {str(e)}")
            return None
    
    async def _alert_and_wait(self, operation_name: str):
        logger.error(f"ALERT: Critical data fetch failed for {operation_name} - waiting before retry")
        await asyncio.sleep(30)
    
    def _record_success(self, operation_name: str, level: RecoveryLevel):
        if len(self.recovery_state) >= self.max_state_size:
            oldest_key = min(
                self.recovery_state.keys(),
                key=lambda k: self.recovery_state[k].get("last_success", "") or ""
            )
            if oldest_key in self.recovery_state:
                del self.recovery_state[oldest_key]
                logger.debug(f"Evicted oldest recovery state: {oldest_key}")
        
        if operation_name not in self.recovery_state:
            self.recovery_state[operation_name] = {
                "success_count": 0,
                "level_counts": {l.name: 0 for l in RecoveryLevel}
            }
        
        self.recovery_state[operation_name]["success_count"] += 1
        self.recovery_state[operation_name]["level_counts"][level.name] += 1
        self.recovery_state[operation_name]["last_success"] = datetime.now(timezone.utc).isoformat()
    
    def get_recovery_stats(self, operation_name: str) -> Optional[Dict]:
        return self.recovery_state.get(operation_name)
