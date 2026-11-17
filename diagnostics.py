"""Readable console errors and local support records; never sends diagnostics."""
import hashlib
import os
from pathlib import Path
import sys
import traceback


def readable(value, limit=800):
    """Keep device output readable without letting terminal controls execute."""
    return "".join(c for c in value if c.isprintable() or c == "\n")[-limit:]


def failure(error, root, **context):
    manifest = Path(root) / "NATIVE_SHA256.json"
    try:
        package = hashlib.sha256(manifest.read_bytes()).hexdigest()
    except OSError:
        package = None
    return {"schema": "onn26-error-v1", "error_type": type(error).__name__, "reason": str(error),
            "traceback": "".join(traceback.format_exception(type(error), error, error.__traceback__)),
            "python": sys.version, "platform": sys.platform, "machine": os.uname().machine,
            "package_manifest_sha256": package, "automatic_retry": False, **context}
