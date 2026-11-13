"""Reconnect fixtures never call ADB, launch processes, or access the network."""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import connection as c
from connection_identity import select_unique
import onn2026 as o
import root_workflow as flow

OLD = "192.0.2.71:5555"
NEW = "192.0.2.88:5555"
THIRD = "192.0.2.99:5555"
TLS_NAME = "adb-JUSADEMOA-fixture._adb-tls-connect._tcp"
BINDING = {"ro.serialno": "JUSADEMOA", "ro.product.device": o.PROFILE["device"],
           "ro.build.fingerprint": o.PROFILE["fingerprint"], "ro.bootloader": o.PROFILE["bootloader"],
           "ro.boot.slot_suffix": "_b"}


def props(serial="JUSADEMOA", **changes):
    return {**BINDING, "ro.product.cpu.abilist": o.PROFILE["abi"], "ro.serialno": serial, **changes}


class FakeSession(o.Session):
    def __init__(self, work, connected=None, available=None, properties=None, mdns="", mdns_failed=False, aliases=None):
        super().__init__(work, "offline-reconnect-test")
        self.connected = dict(connected or {})
        self.available = dict(available or {})
        self.properties = dict(properties or {})
        self.calls = []
        self.reads = {}
        self.on_read = lambda target, count: None
        self.mdns, self.mdns_failed = mdns, mdns_failed
        self.aliases = dict(aliases or {})

    def run(self, label, argv, **kwargs):
        self.calls.append(list(argv))
        if argv[:2] == ["fake-adb", "connect"] and len(argv) == 3:
            if argv[2] in self.available:
                self.connected[self.aliases.get(argv[2], argv[2])] = self.available[argv[2]]
            output = ""
        elif argv == ["fake-adb", "devices", "-l"]:
            output = "List of devices attached\n" + "\n".join(
                f"{target}\t{state}" for target, state in self.connected.items())
        elif argv == ["fake-adb", "mdns", "services"]:
            return {"returncode": 1 if self.mdns_failed else 0, "timed_out": self.mdns_failed,
                    "stdout": self.mdns, "stderr": ""}
        elif len(argv) == 5 and argv[:2] == ["fake-adb", "-s"] and argv[3:] == ["shell", "getprop"]:
            target = argv[2]
            if self.connected.get(target) != "device":
                raise AssertionError("Properties read from unauthorized/offline device")
            self.reads[target] = self.reads.get(target, 0) + 1
            self.on_read(target, self.reads[target])
            values = self.properties[target]
            if isinstance(values, list):
                values = values[min(self.reads[target] - 1, len(values) - 1)]
            output = "\n".join(f"[{key}]: [{value}]" for key, value in values.items())
        else:
            raise AssertionError("Unexpected or mutating command: " + repr(argv))
        return {"returncode": 0, "timed_out": False, "stdout": output, "stderr": ""}


class Connections(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        finder = patch.object(o.shutil, "which", return_value="fake-adb")
        finder.start()
        self.addCleanup(finder.stop)
        # These guards cover accidental escapes from the fake transport.
        for owner, name in ((o.subprocess, "run"), (c.socket, "create_connection")):
            guard = patch.object(owner, name, side_effect=AssertionError("Live I/O forbidden"))
            guard.start()
            self.addCleanup(guard.stop)
        self.path = Path(self.tmp.name) / "saved.json"
        o.save(self.path, {"schema": c.SCHEMA, "binding": BINDING, "target": OLD,
                           "name": "Living room", "updated_at": "original"})
        self.original = self.path.read_bytes()

    def device(self, **kwargs):
        session = FakeSession(self.tmp.name, **kwargs)
        device = o.Device(session, OLD, "fake-adb")
        device.binding = dict(BINDING)
        device.connection_path = self.path
        return device

    def test_old_ip_other_onn_new_ip_selected_and_attempts_preserved(self):
        device = self.device(connected={OLD: "device", NEW: "device"},
                             properties={OLD: props("JUSADEMOB"), NEW: props()})
        attempt = flow.journal(device, "root-install-attempt.json")
        o.save(attempt, {"attempt_consumed": True}, exclusive=True)
        before = attempt.read_bytes()
        with patch.object(c, "scan", side_effect=AssertionError("Unneeded LAN scan")):
            result = c.reconnect(device, self.path, scan_lan=True)
        self.assertEqual(result["target"], NEW)
        self.assertTrue(result["ip_changed"])
        self.assertEqual((result["device_write_invocations"], result["reboot_invocations"]), (0, 0))
        self.assertEqual(c.load(self.path)["name"], "Living room")
        self.assertEqual(attempt.read_bytes(), before)

    def test_same_ip_is_verified_without_replaying_work(self):
        device = self.device(connected={OLD: "device"}, properties={OLD: props()})
        result = c.reconnect(device, self.path)
        self.assertFalse(result["ip_changed"])
        self.assertEqual(device.s.reads, {OLD: 2})

    def test_paired_mdns_transport_is_usable_when_adb_lists_no_ip_address(self):
        endpoint = "192.0.2.71:37123"
        device = self.device(connected={TLS_NAME: "device"}, properties={TLS_NAME: props()},
                             mdns="adb-JUSADEMOA-fixture _adb-tls-connect._tcp " + endpoint)
        with patch.object(c, "scan", side_effect=AssertionError("Connected device must not need LAN scan")):
            result = c.reconnect(device, self.path, scan_lan=True)
        self.assertEqual(result["target"], TLS_NAME)
        self.assertEqual(device.s.reads, {TLS_NAME: 2})
        self.assertEqual(c.load(self.path)["last_network_address"], endpoint)
        # A saved service name must also work on a later guide step.
        self.assertEqual(c.reconnect(device, self.path)["target"], TLS_NAME)

    def test_explicit_ip_can_resolve_its_paired_transport_without_reading_other_devices(self):
        other = "adb-JUSAOTHER-fixture._adb-tls-connect._tcp"
        device = self.device(connected={TLS_NAME: "device", other: "device"}, properties={TLS_NAME: props()},
                             mdns="adb-JUSADEMOA-fixture _adb-tls-connect._tcp " + NEW + "\n"
                                  "adb-JUSAOTHER-fixture _adb-tls-connect._tcp " + THIRD)
        self.assertEqual(c.reconnect(device, self.path, candidate=NEW)["target"], TLS_NAME)
        self.assertEqual(device.s.reads, {TLS_NAME: 2})

    def test_mdns_connect_can_create_a_service_name_transport(self):
        device = self.device(available={NEW: "device"}, aliases={NEW: TLS_NAME}, properties={TLS_NAME: props()},
                             mdns="adb-JUSADEMOA-fixture _adb-tls-connect._tcp " + NEW)
        self.assertEqual(c.reconnect(device, self.path)["target"], TLS_NAME)
        self.assertEqual(c.load(self.path)["last_network_address"], NEW)

    def test_saved_tls_transport_falls_back_to_prepared_port_without_mdns(self):
        c.replace_record(self.path, {**c.load(self.path), "target": TLS_NAME, "last_network_address": "192.0.2.71:37123"})
        device = self.device(available={OLD: "device"}, properties={OLD: props()}, mdns_failed=True)
        with patch.object(c, "scan", side_effect=AssertionError("Known IP should work without LAN scan")):
            self.assertEqual(c.reconnect(device, self.path, scan_lan=True)["target"], OLD)

    def test_unauthorized_service_names_and_pairing_services_are_not_read(self):
        pairing = "adb-JUSADEMOA-fixture._adb-tls-pairing._tcp"
        device = self.device(connected={TLS_NAME: "unauthorized", pairing: "device"})
        with self.assertRaises(ValueError):
            c.reconnect(device, self.path)
        self.assertEqual(device.s.reads, {})
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_another_device_using_a_plausible_service_name_is_rejected(self):
        device = self.device(connected={TLS_NAME: "device"}, properties={TLS_NAME: props("JUSAOTHER")})
        with self.assertRaises(ValueError):
            c.reconnect(device, self.path)
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_new_device_selection_accepts_the_transport_for_the_entered_ip(self):
        import guide as g
        device = self.device(connected={TLS_NAME: "device"}, properties={TLS_NAME: props()},
                             mdns="adb-JUSADEMOA-fixture _adb-tls-connect._tcp " + NEW)
        device.binding = None
        device.connection_path = None
        args = o.parser().parse_args(["--work", self.tmp.name, "--target", NEW, "guide"])
        ui = g.UI(read=lambda prompt: "", choose=lambda: True, emit=lambda message: None, clear=lambda: None)
        with patch.object(o, "Session", return_value=device.s):
            selected = g.select_connection(args, ui)
        saved = c.load(selected)
        self.assertEqual(saved["target"], TLS_NAME)
        self.assertEqual(saved["last_network_address"], NEW)
        self.assertEqual(saved["binding"], BINDING)

    def test_atomic_completion_publication_never_overwrites_existing_receipt(self):
        destination = Path(self.tmp.name) / "completion.json"
        c.create_record(destination, {"complete": True})
        with self.assertRaises(FileExistsError):
            c.create_record(destination, {"complete": False})
        self.assertEqual(json.loads(destination.read_text()), {"complete": True})
        self.assertFalse(list(destination.parent.glob(".completion.json.*.tmp")))

    def test_explicit_candidate_reads_only_that_device(self):
        device = self.device(connected={OLD: "device", THIRD: "device"},
                             available={NEW: "device"}, properties={NEW: props()})
        c.reconnect(device, self.path, candidate=NEW)
        self.assertEqual(device.s.reads, {NEW: 2})

    def test_unauthorized_and_offline_devices_never_receive_shell(self):
        device = self.device(connected={OLD: "unauthorized", NEW: "offline"})
        with self.assertRaisesRegex(ValueError, "not found and authorized"):
            c.reconnect(device, self.path)
        self.assertEqual(device.s.reads, {})
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_wrong_serial_firmware_slot_or_abi_cannot_replace_saved_address(self):
        for key in (*o.IDENTITY, "ro.product.cpu.abilist"):
            with self.subTest(key=key):
                device = self.device(connected={NEW: "device"}, properties={NEW: props(**{key: "wrong"})})
                with self.assertRaises(ValueError):
                    c.reconnect(device, self.path)
                self.assertEqual(self.path.read_bytes(), self.original)

    def test_ambiguous_identity_requires_explicit_candidate(self):
        device = self.device(connected={OLD: "device", NEW: "device"}, properties={OLD: props(), NEW: props()})
        with self.assertRaisesRegex(ValueError, "More than one address"):
            c.reconnect(device, self.path)
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_identity_changes_before_save_preserves_old_address(self):
        device = self.device(connected={NEW: "device"}, properties={NEW: [props(), props("JUSADEMOB")]})
        with self.assertRaisesRegex(o.Stop, "saved binding"):
            c.reconnect(device, self.path)
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_scan_only_when_needed_and_selects_saved_serial(self):
        device = self.device(connected={OLD: "device"}, available={NEW: "device", THIRD: "device"},
                             properties={OLD: props("JUSADEMOB"), NEW: props(), THIRD: props("JUSADEMOC")})
        with patch.object(c, "scan", return_value=[NEW, THIRD]) as scan:
            result = c.reconnect(device, self.path, scan_lan=True)
        scan.assert_called_once_with(device.s)
        self.assertEqual(result["target"], NEW)
        self.assertEqual(device.s.reads, {OLD: 1, NEW: 2, THIRD: 1})

    def test_mdns_finds_changed_tls_port_and_ignores_pairing_port(self):
        endpoint = "192.0.2.71:37123"
        device = self.device(available={endpoint: "device"}, properties={endpoint: props()},
                             mdns="List of discovered mdns services\n"
                                  "adb-fixture _adb-tls-connect._tcp " + endpoint + "\n"
                                  "adb-pair _adb-tls-pairing._tcp 192.0.2.71:39000\n")
        with patch.object(c, "scan", side_effect=AssertionError("Unneeded LAN scan")):
            result = c.reconnect(device, self.path, scan_lan=True)
        self.assertEqual(result["target"], endpoint)
        self.assertTrue(result["ip_changed"])
        self.assertEqual(device.s.reads, {endpoint: 2})
        self.assertFalse(any("192.0.2.71:39000" in command for command in device.s.calls))

    def test_mdns_advertised_name_does_not_substitute_for_verified_serial(self):
        device = self.device(available={NEW: "device"}, properties={NEW: props("JUSAOTHER")},
                             mdns="adb-JUSADEMOA _adb-tls-connect._tcp " + NEW)
        with self.assertRaisesRegex(ValueError, "not found and authorized"):
            c.reconnect(device, self.path)
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_failed_mdns_falls_back_to_bounded_lan_scan(self):
        device = self.device(available={NEW: "device"}, properties={NEW: props()}, mdns_failed=True)
        with patch.object(c, "scan", return_value=[NEW]) as scan:
            self.assertEqual(c.reconnect(device, self.path, scan_lan=True)["target"], NEW)
        scan.assert_called_once_with(device.s)

    def test_root_dsu_port_is_found_at_saved_ip_without_multicast_or_lan_scan(self):
        record = {**c.load(self.path), "target": "192.0.2.71:37123"}
        c.replace_record(self.path, record)
        device = self.device(available={OLD: "device"}, properties={OLD: props()}, mdns_failed=True)
        with patch.object(c, "scan", side_effect=AssertionError("Unneeded LAN scan")):
            result = c.reconnect(device, self.path, scan_lan=True)
        self.assertEqual(result["target"], OLD)
        self.assertTrue(result["ip_changed"])
        self.assertEqual(device.s.reads, {OLD: 2})

    def test_explicit_candidate_skips_mdns_and_lan_discovery(self):
        device = self.device(available={NEW: "device"}, properties={NEW: props()},
                             mdns="adb-other _adb-tls-connect._tcp " + THIRD)
        with patch.object(c, "discover_mdns", side_effect=AssertionError("Unrequested discovery")), \
             patch.object(c, "scan", side_effect=AssertionError("Unrequested scan")):
            c.reconnect(device, self.path, candidate=NEW)

    def test_mdns_wrong_slot_and_ambiguous_identity_preserve_saved_address(self):
        for values in ({NEW: props(**{"ro.boot.slot_suffix": "_a"})}, {NEW: props(), THIRD: props()}):
            with self.subTest(values=values):
                device = self.device(available={key: "device" for key in values}, properties=values,
                                     mdns="\n".join("adb-fixture-" + str(i) + " _adb-tls-connect._tcp " + key
                                                    for i, key in enumerate(values)))
                with self.assertRaises(ValueError):
                    c.reconnect(device, self.path)
                self.assertEqual(self.path.read_bytes(), self.original)

    def test_connection_file_edit_during_discovery_is_not_overwritten(self):
        device = self.device(connected={NEW: "device"}, properties={NEW: props()})
        changed = {**c.load(self.path), "target": THIRD}
        def on_read(target, count):
            if count == 2:
                o.save(self.path, changed)
        device.s.on_read = on_read
        with self.assertRaisesRegex(o.Stop, "changed during discovery"):
            c.reconnect(device, self.path)
        self.assertEqual(c.load(self.path), changed)

    def test_interrupted_connection_replace_preserves_valid_original(self):
        with patch.object(c.os, "replace", side_effect=OSError("simulated interrupted rename")):
            with self.assertRaises(OSError):
                c.replace_record(self.path, {**c.load(self.path), "target": NEW})
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertFalse(list(self.path.parent.glob(".*.tmp")))

    def test_two_household_onns_have_separate_connections_and_attempts(self):
        first = self.device()
        first.connection_path = None
        first_path = Path(c.remember(first, "Living room"))
        second = self.device()
        second.binding = {**BINDING, "ro.serialno": "JUSADEMOB"}
        second.connection_path = None
        second.target = NEW
        second_path = Path(c.remember(second, "Bedroom"))
        self.assertNotEqual(first_path, second_path)
        self.assertNotEqual(flow.journal(first, "unlock-attempt.json"), flow.journal(second, "unlock-attempt.json"))
        o.save(flow.journal(first, "unlock-attempt.json"), {"consumed": True}, exclusive=True)
        self.assertFalse(flow.journal(second, "unlock-attempt.json").exists())
        rows = c.devices(first.s.work)["saved_devices"]
        self.assertEqual({row["name"] for row in rows}, {"Living room", "Bedroom"})
        self.assertEqual(c.load(first_path)["binding"], BINDING)
        c.remember(first)
        self.assertEqual(c.load(first_path)["name"], "Living room")

    def test_saved_connection_prevents_cross_device_status_before_trusted_read(self):
        device = self.device(connected={OLD: "device"}, properties={OLD: props("JUSADEMOB")})
        with patch.object(o, "Session", return_value=device.s), patch.object(o, "lock_state") as trusted:
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                code = o.main(["--connection", str(self.path), "status"])
        self.assertEqual(code, 2)
        trusted.assert_not_called()
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_initial_status_saves_named_connection(self):
        device = self.device(connected={OLD: "device"}, properties={OLD: props()})
        device.binding = None
        device.connection_path = None
        with patch.object(o, "lock_state", return_value=(1, "example-boot")):
            result = o.status(device, "Living room")
        saved = c.load(result["connection_file"])
        self.assertEqual(saved["binding"], BINDING)
        self.assertEqual(saved["name"], "Living room")

    def test_corrupt_or_unsupported_saved_binding_is_rejected(self):
        for record in ({}, {"schema": "wrong"}, {"schema": c.SCHEMA, "binding": {}},
                       {**c.load(self.path), "binding": {**BINDING, "ro.boot.slot_suffix": "_c"}}):
            with self.subTest(record=record):
                path = Path(self.tmp.name) / "bad.json"
                o.save(path, record)
                with self.assertRaises((o.Stop, ValueError)):
                    c.load(path)

    def test_no_saved_binding_cannot_select_a_plausible_device(self):
        with self.assertRaises(ValueError):
            select_unique({}, [{"state": "device", "target": NEW, "properties": props()}])


class NetworkBounds(unittest.TestCase):
    @staticmethod
    def interface(address, name="enp1s0", prefix=16, flags=None):
        return {"ifname": name, "flags": flags or ["UP", "LOWER_UP"],
                "addr_info": [{"family": "inet", "scope": "global", "local": address, "prefixlen": prefix}]}

    def test_scan_is_bounded_to_private_active_lans_and_narrow_prefixes(self):
        interfaces = [self.interface("192.168.12.34"), self.interface("10.9.8.7", "wlan0", 28)]
        interfaces += [self.interface("10.5.2.3", name) for name in c.EXCLUDED_INTERFACES]
        interfaces += [self.interface("172.16.2.3", "enp2s0", flags=["UP"]),
                       self.interface("172.16.3.4", "ppp0", flags=["UP", "LOWER_UP", "POINTOPOINT"]),
                       self.interface("203.0.113.9", "enp3s0")]
        networks, own = c.active_lans(interfaces)
        self.assertEqual({str(n) for n in networks}, {"192.168.12.0/24", "10.9.8.0/28"})
        self.assertEqual(own, {"192.168.12.34", "10.9.8.7"})
        self.assertLessEqual(sum(n.num_addresses for n in networks), 272)

    def test_mdns_endpoints_are_bounded_literal_addresses(self):
        self.assertEqual(c.mdns_targets("a _adb._tcp. 192.0.2.71:5555\n"
                                       "b _adb-tls-connect._tcp 192.0.2.71:5555\n"
                                       "c _adb-tls-connect._tcp hostname:5555\n"
                                       "d _adb-tls-connect._tcp 192.0.2.71:0\n"
                                       "e _adb-tls-connect._tcp 127.0.0.1:5555\n"
                                       "f _adb-tls-pairing._tcp 192.0.2.72:12345"), [OLD])
        with self.assertRaisesRegex(o.Stop, "Too many advertised"):
            c.mdns_targets("\n".join(f"a{i} _adb-tls-connect._tcp 192.0.2.{i}:5555" for i in range(1, 34)))

    def test_conflicting_mdns_addresses_and_shell_fragments_are_rejected(self):
        with self.assertRaisesRegex(o.Stop, "Conflicting"):
            c.mdns_services("adb-fixture _adb-tls-connect._tcp " + NEW + "\n"
                            "adb-fixture _adb-tls-connect._tcp " + THIRD)
        self.assertEqual(c.adb_devices("bad;name._adb-tls-connect._tcp device\n"
                                      "adb-fixture._adb-tls-pairing._tcp device\n"), {})
        with self.assertRaises(o.Stop):
            c.network_target(TLS_NAME)

    def test_more_than_four_lans_refuses_scan(self):
        with self.assertRaisesRegex(o.Stop, "More than four"):
            c.active_lans([self.interface(f"192.168.{i}.2", f"enp{i}s0") for i in range(5)])

    def test_usb_emulators_and_malformed_addresses_are_excluded(self):
        rows = c.adb_devices("List of devices attached\nUSB123 device\nemulator-5554 device\n"
                             f"{NEW} unauthorized\n127.0.0.1:5555 device\n192.0.2.1:99999 device\n")
        self.assertEqual(rows, {NEW: "unauthorized"})
        for value in ("hostname", "192.0.2.1;reboot", "0.0.0.0", "192.0.2.1:0", "224.0.0.1", "300.1.2.3"):
            with self.subTest(value=value), self.assertRaises((ValueError, o.Stop)):
                c.network_target(value)

    def test_scan_probes_only_the_bounded_subnet_excluding_the_host(self):
        calls, probed = [], []
        def run(label, argv, **kwargs):
            calls.append(argv)
            return {"stdout": json.dumps([self.interface("192.168.12.10")])}
        def probe(address):
            probed.append(address)
            return "192.168.12.71:5555" if address == "192.168.12.71" else None
        with patch.object(c, "probe", side_effect=probe):
            found = c.scan(SimpleNamespace(run=run))
        self.assertEqual(found, ["192.168.12.71:5555"])
        self.assertEqual(calls, [["ip", "-j", "-4", "addr", "show", "scope", "global"]])
        self.assertEqual(len(probed), 253)
        self.assertEqual(len(probed), len(set(probed)))
        self.assertNotIn("192.168.12.10", probed)
        self.assertTrue(all(address.startswith("192.168.12.") for address in probed))

    def test_scan_stops_if_many_ports_are_open(self):
        session = SimpleNamespace(run=lambda *a, **k: {"stdout": json.dumps([self.interface("192.168.12.10")])})
        with patch.object(c, "probe", side_effect=lambda ip: ip + ":5555"):
            with self.assertRaisesRegex(o.Stop, "Too many open ADB ports"):
                c.scan(session)

    def test_probe_timeout_is_a_single_finite_read_only_connection(self):
        with patch.object(c.socket, "create_connection", side_effect=TimeoutError) as create:
            self.assertIsNone(c.probe("192.168.12.71"))
        create.assert_called_once_with(("192.168.12.71", 5555), timeout=0.3)
        with patch.object(c.socket, "create_connection") as create:
            self.assertEqual(c.probe("192.168.12.71"), "192.168.12.71:5555")
        create.return_value.__exit__.assert_called_once()


if __name__ == "__main__":
    unittest.main()
