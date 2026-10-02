"""Run the real service with the Kernel stand-in."""

import sys

import fakekernel

fakekernel.install()

import uvicorn  # noqa: E402

from zhiwo.api.app import create_app  # noqa: E402

fakekernel.patch_loaded()
uvicorn.run(create_app(), host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
