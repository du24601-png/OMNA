"""Start the unchanged stdio bridge on Linux. Only the platform gate is bypassed."""

import sys

import zhiwo.gateway.stdio_bridge as bridge


class _Sys:
    platform = "win32"

    def __getattr__(self, name):
        return getattr(sys, name)


bridge.sys = _Sys()
bridge.main()
