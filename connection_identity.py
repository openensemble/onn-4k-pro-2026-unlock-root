"""Pure identity matching, shared with the offline demo. No I/O or device calls."""
import re

FIELDS = ("ro.serialno", "ro.product.device", "ro.build.fingerprint", "ro.bootloader", "ro.boot.slot_suffix")


def validate_binding(binding):
    if not isinstance(binding, dict) or any(not isinstance(binding.get(k), str) or not binding[k] for k in FIELDS):
        raise ValueError("A complete saved hardware and firmware binding is required")
    if not re.fullmatch(r"JUSA[A-Za-z0-9]+", binding["ro.serialno"]):
        raise ValueError("Invalid saved hardware serial")
    return {key: binding[key] for key in FIELDS}


def matches(binding, properties):
    expected = validate_binding(binding)
    return isinstance(properties, dict) and all(properties.get(k) == v for k, v in expected.items())


def matching_targets(binding, candidates):
    validate_binding(binding)
    return sorted({row["target"] for row in candidates
                   if row.get("state") == "device" and isinstance(row.get("target"), str)
                   and matches(binding, row.get("properties"))})


def select_unique(binding, candidates):
    found = matching_targets(binding, candidates)
    if not found:
        raise ValueError("The saved onn was not found and authorized; no target was changed")
    if len(found) != 1:
        raise ValueError("More than one address claims the saved identity; use one explicit candidate")
    return found[0]
