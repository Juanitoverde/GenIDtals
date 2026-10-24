#!/usr/bin/env python3
"""Convenience entry point: ``python genidtals.py <command> ...``."""

import sys

from genidtals.cli import main

if __name__ == "__main__":
    sys.exit(main())
