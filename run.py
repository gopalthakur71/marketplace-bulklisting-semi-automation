"""Entry point for the Myntra bulk-listing pipeline.

Marketplace pipelines live under src/<marketplace>/ (shared code in src/core/).
This thin wrapper runs the Myntra one: `python run.py`.
"""
from src.myntra.pipeline import cli

if __name__ == "__main__":
    cli()
