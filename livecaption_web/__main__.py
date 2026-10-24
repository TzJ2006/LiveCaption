"""Run the LiveCaption web UI without a running Personal Agents service."""
import sys


def main():
    from core.app import main as serve
    serve(["--feature", "caption", *sys.argv[1:]])


if __name__ == "__main__":
    main()
