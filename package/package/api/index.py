"""Vercel serverless entrypoint.

Vercel discovers Python functions inside the ``api/`` directory, so this file
exposes the Flask ``app`` for the platform. All request routing is sent here via
the rewrite in ``vercel.json``; static assets under ``public/`` are served
directly by Vercel's CDN and never reach this function.
"""
import os
import sys

# Make the project root importable so ``w3admin`` resolves from inside api/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from w3admin import create_app  # noqa: E402

app = create_app()
