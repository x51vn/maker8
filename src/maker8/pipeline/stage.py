"""Abstract base class for pipeline stages."""

from __future__ import annotations

from abc import ABC, abstractmethod

from maker8.models.common import RenderStage
from maker8.pipeline.context import PipelineContext


class Stage(ABC):
    """One step in the render pipeline.

    Subclasses set ``name`` to the ``RenderStage`` enum value and implement
    ``execute()``.  The orchestrator calls stages sequentially.
    """

    @property
    @abstractmethod
    def name(self) -> RenderStage: ...

    @abstractmethod
    def execute(self, ctx: PipelineContext) -> None:
        """Run the stage, mutating *ctx* in place.

        Raise ``StageError`` on failure.
        """
        ...
