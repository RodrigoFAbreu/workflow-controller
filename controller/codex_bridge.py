"""The Codex tester's sandbox bridge (C10g-3b).

Puts a captured :class:`controller.functional_review.Candidate` into the Codex tester's folder: the read-only
clone, the scratch bare origin, the output folder and the writable-location targets (all inside the profile's one
write area), the Controller-only check directory and item record outside it, and everything
:func:`controller.codex_sandbox.build` writes. The clone sequence and the binding comparison are the Claude
tester's own (``functional_review.clone_candidate`` and ``bound_differences``), so the two testers share one set of
checkout rules. Nothing calls this module yet: no ``codex`` is started and no role is declared (see
``docs/ai-workflow/CONTROLLER_CODEX_TESTER_BRIDGE_PLAN.md``).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from . import codex_sandbox, functional_review, runtime
from .functional_review import Candidate, RefusedPattern


@dataclasses.dataclass(frozen=True)
class Bridge:
    """What :func:`build_bridge` prepared for one Codex tester job, and what was recorded about the clone before
    the tester starts."""

    built: codex_sandbox.Built
    candidate: Candidate
    work_item_id: str
    writable: tuple[str, ...]
    refused: tuple[RefusedPattern, ...]
    git_state: Mapping[str, tuple]
    refs: Mapping[str, str]

    @property
    def layout(self) -> codex_sandbox.TesterLayout:
        return self.built.layout

    @property
    def clone(self) -> Path:
        return self.layout.clone

    @property
    def origin(self) -> Path:
        """The scratch bare origin the clone's ``origin`` remote points at (inside the write area)."""
        return self.layout.scratch / "origin.git"

    @property
    def out(self) -> Path:
        """The output folder for ``RESULTS.json`` and the logs (inside the write area)."""
        return self.layout.scratch / "out"

    @property
    def writable_dir(self) -> Path:
        """The targets of the writable-location links (inside the write area)."""
        return self.layout.scratch / "writable"

    @property
    def writable_links(self) -> dict[str, Path]:
        return {name: self.writable_dir / name for name in self.writable}

    @property
    def check_dir(self) -> Path:
        """The empty Git directory ignore rules are evaluated against: the Controller's, outside every profile entry."""
        return self.layout.base / "check.git"


def build_bridge(candidate: Candidate, *, runtime_root: Path, job_id: str, work_item_id: str, operator_home: Path,
                 binary: Path, **options: Any) -> Bridge:
    """Build the Codex tester's folder from ``candidate`` and verify the fresh clone against the manifest (D4).
    ``options`` go to :func:`codex_sandbox.build` unchanged. A folder that already exists refuses and is left
    alone; any other failure, a checkout conversion included (``functional_tester_unavailable`` with cause
    ``clone_mismatch``), removes the folder before anything runs."""
    layout = codex_sandbox.TesterLayout(runtime_root, job_id)
    base = layout.base
    runtime.assert_contained(runtime_root, base)
    if base.exists():
        raise functional_review.unavailable(functional_review.UNAVAILABLE_ENFORCEMENT,
                                            f"the sandbox {base} already exists")
    try:
        built = codex_sandbox.build(runtime_root=runtime_root, job_id=job_id, operator_home=operator_home,
                                    binary=binary, **options)
        runtime.write_bytes(runtime_root, layout.rel("ITEM"), work_item_id.encode())
        scratch = layout.scratch
        (scratch / "out").mkdir(parents=True, exist_ok=True)
        functional_review._plain(base, "init", "-q", "--bare", "check.git")
        functional_review.clone_candidate(candidate, clone=layout.clone, origin=scratch / "origin.git")
        writable, refused = functional_review.derive_writable_locations(candidate.root_gitignore, candidate.manifest)
        functional_review.make_writable_links(layout.clone, scratch / "writable", writable)
        functional_review.exclude_writable_links(runtime_root, layout.rel("tree", ".git"), writable)
        bridge = Bridge(built, candidate, work_item_id, writable, refused,
                        functional_review._git_dir_state(layout.clone / ".git"),
                        functional_review.refs_of(layout.clone / ".git"))
        differences = functional_review.walk_differences(layout.clone, candidate, bridge.check_dir,
                                                         bridge.writable_links)
        if differences:
            raise functional_review.unavailable(
                functional_review.UNAVAILABLE_CLONE_MISMATCH,
                f"the fresh clone does not match the manifest (a checkout conversion?): {differences[0]}",
                differences=differences[:10])
        return bridge
    except BaseException:
        functional_review.remove_sandbox(runtime_root, job_id)
        raise
