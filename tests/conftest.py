from __future__ import annotations

import atexit
import os
import shutil
import tempfile


_TEST_DATA_ROOT = tempfile.mkdtemp(prefix="datailor-tests-")


def _cleanup_test_data_root() -> None:
    shutil.rmtree(_TEST_DATA_ROOT, ignore_errors=True)


atexit.register(_cleanup_test_data_root)


def pytest_configure() -> None:
    os.environ.setdefault("DATAILOR_ENABLED", "true")
    os.environ["DATAILOR_DATA_DIR"] = os.path.join(_TEST_DATA_ROOT, "Datailor")
    os.environ["PREFERENCE_UI_DIR"] = os.path.join(_TEST_DATA_ROOT, "ui")
    os.environ["DATAILOR_FITTING_DIR"] = os.path.join(_TEST_DATA_ROOT, "fitting")
