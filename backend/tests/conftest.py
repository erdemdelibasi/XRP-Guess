"""Puts backend/ (this directory's parent) on sys.path so tests can `import
trading`, `import predict`, etc. the same flat way the modules import each
other -- regardless of whether pytest is invoked from backend/ or the repo
root."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
