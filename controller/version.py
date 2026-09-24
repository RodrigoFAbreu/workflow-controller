"""The Controller's single semantic version source.

``pyproject.toml`` reads this value as the dynamic project version
(``[tool.setuptools.dynamic]``), which setuptools evaluates statically
without importing the package. ``workflow-controller --version`` and every
recorded runtime identity read it at runtime. This module holds exactly one
assignment and imports nothing.
"""

__version__ = "1.1.0"
