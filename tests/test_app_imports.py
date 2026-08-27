"""The application must be importable, or the server dies at startup.

A missing import in any routed module (a NameError in a decorator line, for
example) only surfaces when uvicorn imports the app -- v1.0.10 shipped with
exactly that and crashed on boot. Importing the app here makes the whole
module graph part of the test suite.
"""

import importlib


def test_the_application_imports():
    module = importlib.import_module("backend.app.main")
    assert hasattr(module, "app")
