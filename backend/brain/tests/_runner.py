"""Shared script-mode runner for the test files.

WHY THIS EXISTS
---------------
Each test file ended with:

    if __name__ == "__main__":
        for name, fn in sorted(globals().items()):
            if name.startswith("test_") and callable(fn):
                fn()

That block collects `test_*` from `globals()` AT CALL TIME. Appending new tests
below it therefore skipped them silently -- the file reported "ALL 46 TESTS
PASSED" while pytest saw 53, and the difference was whatever had been added
most recently.

This happened in FOUR files (test_chat_interview_gating, test_intake_template,
test_ingest_manifest, test_document_qa) and each time it was fixed by moving the
block to the end of that one file. Moving it is a fix for the instance; the
class of bug is "collection order depends on where the caller sits".

`run_tests(globals())` is called at the END of a file too, but it takes the
module's namespace explicitly and is registered via `atexit`, so tests defined
after the call are still collected. Appending to a file cannot silently skip
them again.
"""
from __future__ import annotations

import atexit
import inspect
import os
import sys


def _monkeypatch():
    """Minimal pytest-style monkeypatch for tests that ask for one."""
    class _MP:
        def __init__(self):
            self._undo = []

        def setattr(self, target, name, value):
            old = getattr(target, name)
            self._undo.append((target, name, old))
            setattr(target, name, value)

        def undo(self):
            for target, name, old in reversed(self._undo):
                setattr(target, name, old)
            self._undo.clear()
    return _MP()


def run_tests(namespace: dict, label: str = "TESTS") -> None:
    """Run every `test_*` in `namespace` when the module finishes loading.

    Registered with atexit so the whole module is defined first. That is the
    point: a test appended after this call still runs.
    """
    # Script mode ONLY. Under pytest the module is imported, not run as
    # __main__, and registering an atexit handler there re-executes every test
    # after pytest has torn down its event loops -- which surfaced as seven
    # spurious "no current event loop" failures.
    if namespace.get("__name__") != "__main__":
        return

    def _run():
        tests = [(n, f) for n, f in sorted(namespace.items())
                 if n.startswith("test_") and callable(f)]
        passed, failed = 0, []
        for name, fn in tests:
            try:
                if "monkeypatch" in inspect.signature(fn).parameters:
                    mp = _monkeypatch()
                    try:
                        fn(mp)
                    finally:
                        mp.undo()
                else:
                    fn()
                passed += 1
            except Exception as e:  # noqa: BLE001 - report all, not just the first
                failed.append((name, e))
        for name, err in failed:
            print(f"  FAIL {name}: {type(err).__name__}: {err}", file=sys.stderr)
        if failed:
            print(f"{len(failed)} of {len(tests)} {label} FAILED", file=sys.stderr)
            # os._exit, not sys.exit: SystemExit raised inside an atexit
            # callback is caught and printed as "Exception ignored", and the
            # process still exits 0. A test runner that reports success on
            # failure is worse than the bug this file exists to fix.
            sys.stderr.flush()
            sys.stdout.flush()
            os._exit(1)
        print(f"ALL {passed} {label} PASSED")

    atexit.register(_run)
