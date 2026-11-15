import multiprocessing
import sys


def _main(argv: list[str]) -> int:
    if "--self-test" in argv:               # packaged-build smoke test; see n1cepdf/selftest.py
        from n1cepdf.selftest import main as selftest_main
        return selftest_main(argv)
    from n1cepdf.ui.main_window import run
    return run(argv)


if __name__ == "__main__":
    multiprocessing.freeze_support()        # no-op unless frozen; guards against child-process re-launch
    sys.exit(_main(sys.argv))
