"""The scenario catalogue: importing this module registers scenarios 1-10
(1-5 in ``lifecycle``, 6-10 in ``features``). The kit's own selftest scenarios
live in ``selftest.py`` and are never part of it."""

from tests.scenarios import features, lifecycle  # noqa: F401
