"""The scenario catalogue: importing this module registers scenarios 1-12
(1-5 in ``lifecycle``, 6-10 in ``features``, 11-12 in ``review``). The kit's own selftest scenarios
live in ``selftest.py`` and are never part of it."""

from tests.scenarios import features, lifecycle, review  # noqa: F401
