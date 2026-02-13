"""Video rendering / composition sub-package.

This package must NOT import from ``pipeline/``.  It receives a
``RenderInput`` dataclass from the pipeline render stage and returns
the composed video path + metadata.
"""
