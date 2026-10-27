import sys

# Python's multiprocessing starts its helpers as `sys.executable -c "..."`. In the
# packaged app sys.executable is Lookhound itself, so without this every helper
# would open a second Lookhound window. Run the helper's code and leave instead.
if "-c" in sys.argv[1:]:
    at = sys.argv.index("-c")
    code = sys.argv[at + 1]
    sys.argv = ["-c"] + sys.argv[at + 2:]
    exec(compile(code, "<string>", "exec"), {"__name__": "__main__"})
    sys.exit(0)

from lookhound.app import main

if __name__ == "__main__":
    main()
