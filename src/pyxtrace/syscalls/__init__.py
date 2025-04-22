"""Select an OS‑specific tracer at import‑time."""
from __future__ import annotations
import platform as _pl

_system = _pl.system()

if _system == "Linux":
    from .linux import SyscallTracer  # type: ignore
elif _system == "Darwin":
    from .darwin import SyscallTracer  # type: ignore
elif _system == "Windows":
    from .windows import SyscallTracer  # type: ignore
else:
    from .dummy import SyscallTracer  # type: ignore

# ensure we always expose *something* called SyscallTracer
assert "SyscallTracer" in globals()