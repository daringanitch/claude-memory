"""Tests for generate_user_profile.py — pure functions only, no DB or filesystem."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import generate_user_profile as gup
