"""The scenario catalogue: importing this module registers scenarios 1-5
(``lifecycle``). Scenarios 6-10 are added by CP4; the kit's own selftest
scenarios live in ``selftest.py`` and are never part of it."""

from tests.scenarios import lifecycle  # noqa: F401
