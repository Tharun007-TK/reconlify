"""
Abstract reconciliation engine interface.

Any reconciliation engine (Reconlify CLI, Pandas, future ML engine)
must implement the ReconciliationEngine Protocol.

Design goals:
- Engines are stateless; all context is in ReconInput
- Engines are swappable at runtime via the registry
- Engine internals are completely opaque to the orchestrator
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar, Protocol, runtime_checkable

from app.services.reconciliation.schemas import ReconInput, ReconOutput


@runtime_checkable
class ReconciliationEngine(Protocol):
    """
    Structural protocol for reconciliation engines.
    Implementing classes do NOT need to inherit from this —
    they just need to satisfy the interface (duck typing).

    Preferred usage: inherit from BaseReconciliationEngine for
    built-in validation and logging helpers.
    """

    #: Human-readable engine name (used in metrics + logs)
    ENGINE_NAME: ClassVar[str]

    #: Semantic version of the engine
    ENGINE_VERSION: ClassVar[str]

    async def reconcile(self, input: ReconInput) -> ReconOutput:
        """
        Perform reconciliation.

        Args:
            input: Validated ReconInput with PR and GSTR-2B records

        Returns:
            ReconOutput with matched, unmatched, potential matches, and metrics

        Raises:
            ReconciliationEngineError: If the engine fails to complete
        """
        ...

    def is_available(self) -> bool:
        """
        Check whether this engine is available in the current environment.
        For CLI-based engines: check that the binary exists and is licensed.
        For pure Python engines: always True.
        """
        ...


class BaseReconciliationEngine(ABC):
    """
    Optional abstract base class providing shared utilities.
    Concrete engines may inherit from this for convenience.
    """

    ENGINE_NAME: ClassVar[str] = "base"
    ENGINE_VERSION: ClassVar[str] = "0.0.0"

    @abstractmethod
    async def reconcile(self, input: ReconInput) -> ReconOutput:
        raise NotImplementedError

    def is_available(self) -> bool:
        return True

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.ENGINE_NAME!r} v={self.ENGINE_VERSION!r}>"


class ReconciliationEngineError(Exception):
    """Raised when a reconciliation engine fails to complete its run."""

    def __init__(
        self,
        message: str,
        engine: str = "unknown",
        run_id: str = "",
        cause: Exception | None = None,
    ) -> None:
        self.engine = engine
        self.run_id = run_id
        self.cause = cause
        super().__init__(f"[{engine}] {message}")
