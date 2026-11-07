"""
UI test server — FOR LOCAL TESTING ONLY.

Runs the real app with the deterministic FakeProvider so the interface can be
clicked through (and screenshotted) without an API key or network access.
YouTube network calls are stubbed to fail fast, which exercises the
"no captions → AI transcript" path. Never use this to serve real users.

    python -m tests.run_test_server  [port]  [data_dir]
"""

from __future__ import annotations

import os
import sys

port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
if len(sys.argv) > 2:
    os.environ["DATA_DIR"] = sys.argv[2]
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
os.environ["GEMINI_API_KEY"] = ""
os.environ["OPENAI_API_KEY"] = ""

import uvicorn  # noqa: E402

from app.services import llm  # noqa: E402
from tests.fake_provider import FakeProvider  # noqa: E402

delay = float(os.environ.get("FAKE_DELAY", "0.6"))
llm.set_provider_override(FakeProvider(delay=delay))

from app.main import app  # noqa: E402

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
