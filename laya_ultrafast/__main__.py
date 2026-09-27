"""CLI dispatcher for python -m laya_ultrafast."""

import sys


def main():
    if "--mcp" in sys.argv:
        sys.argv.remove("--mcp")
        from .mcp import main as mcp_main

        mcp_main()
    else:
        from .demo import main as demo_main

        demo_main()


if __name__ == "__main__":
    main()
