from __future__ import annotations

import sys


def main() -> int:
    from inkobold.app import run
    from inkobold.core.flavor import GREEN_INKOBOLD

    return run(sys.argv, flavor=GREEN_INKOBOLD)


if __name__ == "__main__":
    raise SystemExit(main())
