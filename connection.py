"""Saved-device reconnect. Discovery only reads properties; it never replays work."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import uuid

from connection_identity import matches, select_unique, validate_binding
import onn2026 as o

SCHEMA = "onn26-connection-v1"
PRIVATE_LANS = tuple(ipaddress.IPv4Network(value) for value in
                     ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
EXCLUDED_INTERFACES = ("lo", "docker", "veth", "virbr", "br-", "tun", "tap", "wg", "tailscale")
MDNS_TRANSPORT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\._adb(?:-tls-connect)?\._tcp\.?")


def load(path):
    record = json.loads(Path(path).read_text())
    o.require(isinstance(record, dict) and record.get("schema") == SCHEMA,
              "Invalid connection file; use the path printed by the initial status command")
    binding = validate_binding(record.get("binding"))
    # Check the saved firmware profile without substituting a newly discovered serial.
    o.validate_identity({**binding, "ro.product.cpu.abilist": o.PROFILE["abi"]})
    o.require(isinstance(record.get("target"), str) and
              re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:%\[\]-]{0,199}", record["target"]),
              "Invalid saved ADB target")
    if record.get("last_network_address") is not None:
        network_target(record["last_network_address"])
    return record


def replace_record(path, record):
    """Replace the host address atomically; an interruption retains a valid file."""
    path = Path(path)
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        o.save(temporary, record, exclusive=True)
        os.replace(temporary, path)
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def create_record(path, record):
    """Publish a complete durable receipt once; never expose partial JSON."""
    path = Path(path)
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        o.save(temporary, record, exclusive=True)
        os.link(temporary, path)  # Atomic publication; an existing receipt is never replaced.
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def remember(device, name=None):
    binding = validate_binding(device.binding)
    identity_hash = hashlib.sha256(binding["ro.serialno"].encode()).hexdigest()
    path = getattr(device, "connection_path", None) or (
        device.s.work / "devices" / identity_hash / "connection.json")
    previous = load(path) if path.exists() else {}
    if previous:
        o.require(previous["binding"] == binding,
                  "Saved connection has another firmware binding; preserve it and inspect the receipts")
    if name is not None:
        o.require(name.strip() and len(name) <= 80 and name.isprintable(),
                  "Use a printable device name of 1 to 80 characters")
    if MDNS_TRANSPORT.fullmatch(device.target):
        address = getattr(device, "network_address", None) or previous.get("last_network_address")
    else:
        try:
            address = network_target(device.target)
        except (o.Stop, ValueError):
            address = None  # Explicit status calls may use a USB serial or IPv6 target.
    replace_record(path, {"schema": SCHEMA, "binding": binding,
                          "name": name.strip() if name is not None else previous.get("name", binding["ro.serialno"]),
                          "target": device.target, "last_network_address": address, "updated_at": o.now()})
    return str(path)


def devices(work):
    """List host records without connecting to any box or trusting the cached IP."""
    result = []
    for path in sorted(Path(work).resolve().glob("devices/*/connection.json")):
        record = load(path)
        result.append({"name": record.get("name", record["binding"]["ro.serialno"]),
                       "serial": record["binding"]["ro.serialno"],
                       "last_target": record.get("last_network_address") or record["target"],
                       "connection_file": str(path)})
    return {"saved_devices": result, "device_access": False,
            "next": "Select one connection_file with --connection; cached addresses are not live verification."}


def network_target(value):
    """Canonical literal IPv4 endpoint; no DNS, USB serials or shell fragments."""
    o.require(isinstance(value, str), "Use an IPv4 address, optionally followed by :port")
    match = re.fullmatch(r"([0-9.]+)(?::([0-9]{1,5}))?", value)
    o.require(match, "Reconnect requires a literal IPv4 address, optionally followed by :port")
    address = ipaddress.IPv4Address(match[1])
    port = int(match[2] or "5555")
    o.require(not (address.is_unspecified or address.is_multicast or address.is_loopback)
              and 1 <= port <= 65535, "Invalid network ADB address")
    return f"{address}:{port}"


def adb_devices(output):
    current = {}
    for line in output.splitlines():
        words = line.split()
        if len(words) < 2:
            continue
        if MDNS_TRANSPORT.fullmatch(words[0]):
            current[words[0]] = words[1]
            continue
        try:
            target = network_target(words[0])
        except (ValueError, o.Stop):
            continue
        current[target] = words[1]
    return current


def mdns_services(output):
    """Map connection service names to IP endpoints; exclude pairing services."""
    services = {}
    for line in output.splitlines():
        words = line.split()
        if len(words) != 3 or words[1].rstrip(".") not in ("_adb._tcp", "_adb-tls-connect._tcp"):
            continue
        try:
            endpoint = network_target(words[2])
        except (ValueError, o.Stop):
            continue
        name = words[0] + "." + words[1].rstrip(".")
        if not MDNS_TRANSPORT.fullmatch(name):
            continue
        o.require(name not in services or services[name] == endpoint, "Conflicting ADB service addresses")
        services[name] = endpoint
    o.require(len(services) <= 32, "Too many advertised ADB endpoints; use reconnect --candidate IP:port")
    return services


def mdns_targets(output):
    return sorted(set(mdns_services(output).values()))


def discover_mdns(device):
    result = device.s.run("adb-mdns-services", [device.adb, "mdns", "services"], timeout=5, check=False)
    # Old platform-tools and WSL networks may not support multicast discovery.
    return mdns_services(result["stdout"]) if result["returncode"] == 0 and not result["timed_out"] else {}


def connected_target(device, current, endpoint, services=None):
    """An explicit IP may already be connected under ADB's TLS service name."""
    if current.get(endpoint) == "device":
        return endpoint
    aliases = [name for name, state in current.items() if state == "device" and MDNS_TRANSPORT.fullmatch(name)]
    if not aliases:
        return endpoint
    services = discover_mdns(device) if services is None else services
    found = [name for name in aliases if services.get(name.rstrip(".")) == endpoint]
    o.require(len(found) <= 1, "More than one ADB transport uses this IP:port; inspect the connection list")
    return found[0] if found else endpoint


def active_lans(interfaces):
    """At most four active private LAN /24s (or narrower); exclude VPN/virtual links."""
    o.require(isinstance(interfaces, list), "Unexpected host network information")
    networks, own = set(), set()
    for interface in interfaces:
        name, flags = interface.get("ifname", ""), interface.get("flags", [])
        if (not name or "UP" not in flags or "LOWER_UP" not in flags or "POINTOPOINT" in flags
                or name.startswith(EXCLUDED_INTERFACES)):
            continue
        for item in interface.get("addr_info", []):
            if item.get("family") != "inet" or item.get("scope") != "global":
                continue
            address = ipaddress.IPv4Address(item["local"])
            if not any(address in lan for lan in PRIVATE_LANS):
                continue
            own.add(str(address))
            prefix = int(item["prefixlen"])
            o.require(0 <= prefix <= 32, "Invalid LAN prefix")
            networks.add(ipaddress.IPv4Network((str(address), max(24, prefix)), strict=False))
    o.require(len(networks) <= 4, "More than four LANs found; use reconnect --candidate IP instead")
    return sorted(networks, key=str), own


def probe(address):
    try:
        with socket.create_connection((address, 5555), timeout=0.3):
            return address + ":5555"
    except OSError:
        return None


def scan(session):
    result = session.run("host-lans", ["ip", "-j", "-4", "addr", "show", "scope", "global"], timeout=5)
    networks, own = active_lans(json.loads(result["stdout"]))
    o.require(networks, "No active private IPv4 LAN found; use reconnect --candidate IP")
    addresses = sorted({str(address) for network in networks for address in network.hosts()
                        if str(address) not in own})
    # Only socket probes run concurrently. Session command receipts stay sequential.
    with ThreadPoolExecutor(max_workers=32) as pool:
        found = sorted({value for value in pool.map(probe, addresses) if value})
    o.require(len(found) <= 32, "Too many open ADB ports; use reconnect --candidate IP")
    return found


def inspect(device, current, targets):
    rows = []
    o.require(len(targets) <= 64, "Too many ADB targets; use reconnect --candidate IP")
    for target in sorted(targets):
        row = {"target": target, "state": current.get(target, "absent")}
        if row["state"] == "device":
            result = device.s.run("candidate-properties", [device.adb, "-s", target, "shell", "getprop"],
                                  timeout=5, check=False)
            if result["returncode"] == 0 and not result["timed_out"]:
                properties = o.parse_props(result["stdout"])
                try:
                    o.validate_identity(properties)
                    row["properties"] = properties
                except o.Stop:
                    row["reason"] = "Unsupported firmware or hardware profile"
            else:
                row["reason"] = "Property read failed; no retry"
        rows.append(row)
    return rows


def reconnect(device, connection_path, candidate=None, scan_lan=False):
    o.require(connection_path, "Reconnect needs --connection from the initial status command")
    path = Path(connection_path).resolve()
    original = load(path)
    binding = original["binding"]
    o.require(device.binding == binding, "Connection binding changed before discovery")
    preferred = network_target(candidate) if candidate else original["target"]
    if not MDNS_TRANSPORT.fullmatch(preferred):
        preferred = network_target(preferred)
    device.s.run("adb-connect", [device.adb, "connect", preferred], timeout=5, check=False)
    result = device.s.run("adb-devices", [device.adb, "devices", "-l"], timeout=5)
    current = adb_devices(result["stdout"])
    services = None
    if candidate and current.get(preferred) != "device" and any(MDNS_TRANSPORT.fullmatch(k) for k in current):
        services = discover_mdns(device)
    targets = {connected_target(device, current, preferred, services)} if candidate else set(current) | {preferred}
    rows = inspect(device, current, targets)
    if not candidate and not any(matches(binding, row.get("properties")) for row in rows):
        # The prepared root DSU uses port 5555 even if original Android used TLS.
        # Try that port at the saved IP as well, including on WSL without mDNS.
        services = discover_mdns(device)
        previous_ip = original.get("last_network_address") if MDNS_TRANSPORT.fullmatch(preferred) else preferred
        legacy = {network_target(previous_ip.split(":", 1)[0])} if previous_ip else set()
        discovered = (set(services.values()) | legacy).difference(targets)
        o.require(len(targets | discovered) <= 64, "Too many ADB targets; use reconnect --candidate IP:port")
        for target in sorted(discovered):
            device.s.run("adb-connect-mdns", [device.adb, "connect", target], timeout=5, check=False)
        if discovered:
            result = device.s.run("adb-devices", [device.adb, "devices", "-l"], timeout=5)
            current = adb_devices(result["stdout"])
            # adb connect IP:port may create a TLS service-name transport instead.
            discovered |= set(current).difference(targets)
            o.require(len(targets | discovered) <= 64, "Too many ADB targets; use reconnect --candidate IP:port")
            rows += inspect(device, current, discovered)
            targets.update(discovered)
    if not candidate and scan_lan and not any(matches(binding, row.get("properties")) for row in rows):
        found = scan(device.s)
        o.require(len(targets | set(found)) <= 64, "Too many ADB targets; use reconnect --candidate IP:port")
        for target in found:
            if target not in targets:
                device.s.run("adb-connect", [device.adb, "connect", target], timeout=5, check=False)
        result = device.s.run("adb-devices", [device.adb, "devices", "-l"], timeout=5)
        current = adb_devices(result["stdout"])
        # Do not repeat any earlier property read within this discovery attempt.
        rows += inspect(device, current, set(found).difference(targets))
    o.save(device.s.path / "discovery.json", {"at": o.now(), "candidates": rows,
                                               "automatic_write_retry": False})
    selected = select_unique(binding, rows)
    fresh = o.Device(device.s, selected, device.adb)
    fresh.binding = binding
    fresh.identify()  # Recheck the selected identity immediately before saving its address.
    if MDNS_TRANSPORT.fullmatch(selected):
        services = discover_mdns(device) if services is None else services
        address = services.get(selected.rstrip("."))
    else:
        address = selected
    o.require(load(path) == original, "Connection file changed during discovery; no address saved")
    previous_address = original.get("last_network_address") or (
        original["target"] if not MDNS_TRANSPORT.fullmatch(original["target"]) else None)
    updated = {**original, "target": selected, "last_network_address": address or previous_address, "updated_at": o.now()}
    replace_record(path, updated)
    device.target = selected
    device.network_address = address or previous_address
    result = {"at": o.now(), "connection_file": str(path), "previous_target": original["target"],
              "target": selected, "network_address": address, "ip_changed": original["target"] != selected,
              "same_device_verified": True, "binding": binding,
              "device_write_invocations": 0, "reboot_invocations": 0,
              "next": "Continue the documented verification step; never replay an uncertain write or reboot."}
    o.save(device.s.path / "reconnect.json", result)
    return result
