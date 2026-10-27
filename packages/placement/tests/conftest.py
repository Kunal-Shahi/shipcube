import os


def pytest_configure(config):
    # Tests profile the sample apps on this machine (LocalRunner): that's DEV_MODE.
    # Set for the whole session so module-scoped fixtures see it too; tests that need
    # production behaviour remove it with monkeypatch.
    os.environ["SHIPCUBE_DEV_MODE"] = "1"
