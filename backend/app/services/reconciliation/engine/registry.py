"""
Engine registry and factory.
Provides runtime engine selection with automatic fallback.
"""
from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

import structlog

from app.services.reconciliation.engine.base import (
    BaseReconciliationEngine,
    ReconciliationEngineError,
)

if TYPE_CHECKING:
    pass

logger = structlog.get_logger(__name__)


class EngineType(StrEnum):
    RECONLIFY = "reconlify_cli"
    PANDAS    = "pandas_fallback"
    AUTO      = "auto"           # Pick best available


_ENGINE_PRIORITY: list[EngineType] = [
    EngineType.RECONLIFY,
    EngineType.PANDAS,
]


def get_engine(
    engine_type: EngineType = EngineType.AUTO,
    **kwargs: object,
) -> BaseReconciliationEngine:
    """
    Factory function — returns a ready-to-use engine instance.

    Args:
        engine_type: Which engine to use. AUTO tries RECONLIFY first,
                     falls back to PANDAS if CLI is unavailable.
        **kwargs:    Passed through to the engine constructor.

    Returns:
        An instantiated engine satisfying the ReconciliationEngine protocol.

    Raises:
        ReconciliationEngineError: If no engine is available.
    """
    # Lazy imports to avoid circular deps
    from app.services.reconciliation.engine.reconlify import ReconlifyEngine
    from app.services.reconciliation.engine.pandas_engine import PandasEngine

    registry: dict[EngineType, type[BaseReconciliationEngine]] = {
        EngineType.RECONLIFY: ReconlifyEngine,
        EngineType.PANDAS:    PandasEngine,
    }

    if engine_type == EngineType.AUTO:
        for candidate in _ENGINE_PRIORITY:
            cls = registry[candidate]
            instance = cls(**kwargs)  # type: ignore[arg-type]
            if instance.is_available():
                logger.info(
                    "engine_registry.selected",
                    engine=candidate,
                    reason="auto-detection",
                )
                return instance

        raise ReconciliationEngineError(
            "No reconciliation engine is available. "
            "Install Reconlify CLI or ensure the pandas_fallback is importable.",
            engine="auto",
        )

    if engine_type not in registry:
        raise ReconciliationEngineError(
            f"Unknown engine type '{engine_type}'. "
            f"Valid options: {list(registry.keys())}",
            engine=str(engine_type),
        )

    instance = registry[engine_type](**kwargs)  # type: ignore[arg-type]
    if not instance.is_available():
        logger.warning(
            "engine_registry.unavailable",
            engine=engine_type,
            fallback=EngineType.PANDAS,
        )
        # Graceful fallback
        instance = PandasEngine()

    logger.info("engine_registry.selected", engine=engine_type)
    return instance
