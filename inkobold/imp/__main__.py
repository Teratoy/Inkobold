from __future__ import annotations

import sys


def main() -> int:
    from inkobold.app import run
    from inkobold.core.flavor import INKIMP

    return run(sys.argv, flavor=INKIMP)


if __name__ == "__main__":
    raise SystemExit(main())
