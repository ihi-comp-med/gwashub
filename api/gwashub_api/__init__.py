# The API imports the runner (repo root); make it importable when started via uvicorn --app-dir api.
import os as _os
import sys as _sys

_root = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
if _root not in _sys.path:
    _sys.path.insert(0, _root)
