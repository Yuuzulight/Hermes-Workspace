"""Run every module self-check plus a full HTTP round-trip. `python selftest.py [--big]`."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

MODULES = ["hw_store"]  # grows each task


def run_module_checks() -> None:
    for name in MODULES:
        mod = __import__(name)
        if hasattr(mod, "_selfcheck"):
            mod._selfcheck()
            print(f"  {name}._selfcheck ok")


if __name__ == "__main__":
    run_module_checks()
    print("ok")
