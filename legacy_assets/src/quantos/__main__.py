"""Legacy module entry point, available only through explicit PYTHONPATH use."""

from quantos.interfaces.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
