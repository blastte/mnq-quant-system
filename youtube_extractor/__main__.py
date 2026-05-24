from .utils import ensure_utf8_stdout

ensure_utf8_stdout()

from .cli import main

if __name__ == "__main__":
    main()
