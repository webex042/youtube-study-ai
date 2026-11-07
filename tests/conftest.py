"""
Test configuration.

Every test run uses a throw-away data folder and no real AI key, so tests never
touch your notebooks or spend API credits. AI behaviour is exercised with the
deterministic FakeProvider (tests/fake_provider.py) or the local Gemini API
mock (tests/mock_gemini.py).
"""

from __future__ import annotations

import os
import tempfile

_DATA = tempfile.mkdtemp(prefix="ai-study-lab-tests-")
os.environ["DATA_DIR"] = _DATA
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["APP_ENV"] = "test"
os.environ["GEMINI_API_KEY"] = ""
os.environ["GOOGLE_API_KEY"] = ""
os.environ["OPENAI_API_KEY"] = ""
