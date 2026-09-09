"""Compatibility launcher for installed Bellomberg automation."""
import os

os.environ.setdefault("BELLOMBERG_PROJECT_ROOT", os.path.dirname(os.path.realpath(__file__)))

if __name__ == "__main__":
    import runpy
    runpy.run_module('bellomberg.api.bellomberg_api', run_name="__main__")
else:
    from bellomberg.api.bellomberg_api import *  # noqa: F401,F403
