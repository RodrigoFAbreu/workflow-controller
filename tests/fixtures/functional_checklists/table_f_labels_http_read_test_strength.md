## Functional review checklist
<!-- Copied from docs/ACTIVE_MILESTONE.md of the workflow-manager repository at commit 44b005b (http-read-test-strength). No byte-for-byte check exists in this clone: the commit is not in this repository. -->

Implementation revision 1. The manual functional review runs against the code at head `082e8b7`; `98badd3` is the branch tip and differs from it only in `docs/ai-workflow/WORKFLOW_STATE.json`.

**Verification already in hand (not rerun).** The full gate, `python3 tests/run_all.py`, exited 0 at `082e8b7`: 2322 of 2322 units, 15359 tests (`.ai-review/http-read-test-strength/current/TEST_RESULTS.md`). No file under `src/`, `tests/` or `tools/` has changed since.

### Setup

- Work in a scratch checkout of `milestone/http-read-test-strength`: a `git clone` into a new, empty directory outside this repository. Flows F2 to F5 edit files, so never run them in the repository itself. Nothing is installed; only `python3` and `git` are needed.
- Every `tests/run_all.py` run, `--select` included, first primes the release cache with every tested version, then runs offline. The first run on a machine needs the network or a `WORKFLOW_MANAGER_RELEASE_SOURCE` mirror. On a machine whose cache is populated (`~/.cache/workflow-manager/releases`, about 470 MB), a scratch run needs no network if `XDG_CACHE_HOME` points at a copy of that cache.
- The F1 command, also used for each mutation run: `python3 tests/run_all.py --select test_discovery.py::TestTheDescriptorProbe --select test_discovery.py::TestTheBoundedPieces`.

### Test data

None. Each case builds its own temporary directories and socket pairs and removes them.

### Flows

| id | do | expect |
| --- | --- | --- |
| F1 | On the candidate, run the F1 command. | exit 0; `Ran 18 tests OK`; nothing reported skipped. |
| F2 | In a scratch copy, change `_decref_socketios` in `src/workflow_manager/source.py` to call `self._real_close()` where it calls `self.close()`, then run the F1 command. | exit 1; `test_a_second_reader_keeps_the_fake_socket_open` and `test_a_second_reader_still_reads_after_the_first_closes` fail; no other case fails. |
| F3 | In a scratch copy, point `_FD_DIRECTORIES` in `tests/test_discovery.py` at a directory of regular files, then run `--select test_discovery.py::TestAnExpiredReadLeavesNothingBehind --select test_discovery.py::TestAProxyConnectReplyIsBounded`. | exit 0; the report reads `OK (skipped=N)`: a visible skip, not a silent pass. |
| F4 | In a scratch copy, make `_socket_links` return an empty set, then run the F1 command. | exit 1; `test_links_are_read` and the positive control `test_the_helper_sees_a_socket_it_just_opened` fail. |
| F5 | In a scratch copy, make `_require_socket_links` always raise `unittest.SkipTest`, then run the F1 command. | exit 1; the positive control fails (a failure, not a skip). |

Record each flow's command, exit code and summary line, and the file and line you changed for F2 to F5.

### Sandbox notes (Controller functional tester)

- No flow calls `gh`, `claude`, `codex`, `ssh` or any forge, and none needs the network. F1 to F5 use only `python3` and `git` from the system directories.
- F1, F2, F4 and F5 use `socket.socketpair()` only (in-process Unix sockets), not TCP. The two classes F3 selects use loopback TCP, but with `_FD_DIRECTORIES` patched they skip in `setUp`, before any server or thread starts.
- The descriptor probe needs `/proc/self/fd` or `/dev/fd` to list `socket:` links. If a sandbox hides them, the probe skips with its reason, F1's "nothing skipped" does not hold, and that is an environment outcome, not a `defect`.
- Setup check (not a review result): F1 was run once in a scratch clone of `milestone/http-read-test-strength` with a copied release cache, inside a network namespace with no network (`unshare -rn`). It exited 0 with `Ran 18 tests OK` and nothing skipped. That namespace maps the user to root, so the run needed `run_all.py --allow-root`; a normal, non-root run does not.

### Known limitations and out of scope

- Items 3 to 5 of #128 (message wording and the priming test's tree) and any `src/` change are out of scope.
- The skip path is exercised only by F3's mutation on this Linux host. No non-Linux host is run.
- Mutations run only in scratch copies. The repository is never mutated.
- The implementation reviewer's sandbox could not rerun socket data transfer, so the real-socket reader case was assessed from code and supplied evidence. F1 is the run that exercises it on Linux.
