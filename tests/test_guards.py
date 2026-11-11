import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import onn2026 as o


BOOT = '11111111-2222-3333-4444-555555555555'


def props():
    return {
        'ro.serialno': 'JUSA_TEST_ONLY',
        'ro.product.device': o.PROFILE['device'],
        'ro.build.fingerprint': o.PROFILE['fingerprint'],
        'ro.bootloader': o.PROFILE['bootloader'],
        'ro.boot.slot_suffix': o.PROFILE['slot'],
        'ro.product.cpu.abilist': 'armeabi-v7a,armeabi',
        'ro.boot.flash.locked': '1',
        'ro.boot.verifiedbootstate': 'green',
    }


class FakeDevice:
    def __init__(self, directory, result=None):
        self.s = o.Session(directory, 'test')
        self.binding = {'ro.serialno': 'JUSA_TEST_ONLY'}
        self.calls = []
        self.result = result or {'returncode': None, 'timed_out': True, 'stdout': ''}

    def identify(self, **kwargs):
        return props()

    def boot_id(self):
        return BOOT

    def push_checked(self, source, remote):
        self.calls.append(('push', str(source), remote))

    def shell(self, label, *args, **kwargs):
        self.calls.append((label, *args))
        if label == 'unlock-once':
            journals = list(self.s.work.glob('devices/*/unlock-attempt.json'))
            if len(journals) != 1:
                raise AssertionError('Write invoked without durable host attempt record')
            return self.result
        return {'returncode': 0, 'timed_out': False, 'stdout': ''}


class Guards(unittest.TestCase):
    def test_unsupported_identity_fields(self):
        good = props()
        good['ro.serialno'] = 'JUSA123456'
        o.validate_identity(good)
        for key in ('ro.serialno', 'ro.product.device', 'ro.build.fingerprint',
                    'ro.bootloader', 'ro.boot.slot_suffix', 'ro.product.cpu.abilist'):
            with self.subTest(key=key):
                bad = dict(good, **{key: 'wrong'})
                with self.assertRaises(o.Stop):
                    o.validate_identity(bad)

    def test_root_requires_completed_unlocked_boot(self):
        good = dict(props(), **o.UNLOCKED, **{'sys.boot_completed': '1', 'ro.serialno': 'JUSA123456'})
        o.validate_identity(good, unlocked=True)
        for key in (*o.UNLOCKED, 'sys.boot_completed'):
            with self.subTest(key=key), self.assertRaises(o.Stop):
                o.validate_identity(dict(good, **{key: ''}), unlocked=True)

    def test_bad_or_ambiguous_trusted_getter_responses(self):
        good = {'stage': 'lock_state', 'rc': 0, 'ret': '0x00000000', 'a': 0}
        self.assertEqual(o.parse_lock_state(json.dumps(good)), 0)
        invalid = ['', 'not JSON', '[]', 'null', '{}',
                   json.dumps(good) + '\n' + json.dumps(good)]
        invalid += [json.dumps(dict(good, **changes)) for changes in
                    ({'a': True}, {'a': -1}, {'a': 2}, {'a': '0'},
                     {'rc': -1}, {'ret': '0xffff0001'})]
        for text in invalid:
            with self.subTest(text=text), self.assertRaises(o.Stop):
                o.parse_lock_state(text)

    def test_already_unlocked_never_stages_or_invokes_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            device = FakeDevice(directory)
            with patch.object(o, 'lock_state', return_value=(0, BOOT)):
                result = o.unlock(device, True)
            self.assertTrue(result['already_unlocked'])
            self.assertEqual(result['write_invocations'], 0)
            self.assertEqual(device.calls, [])

    def test_uncertain_write_consumes_journal_and_cannot_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            device = FakeDevice(directory)
            with patch.object(o, 'lock_state', return_value=(1, BOOT)):
                with self.assertRaises(o.Stop):
                    o.unlock(device, True)
                self.assertEqual(sum(c[0] == 'unlock-once' for c in device.calls), 1)
                previous = list(device.calls)
                with self.assertRaises(o.Stop):
                    o.unlock(device, True)
                self.assertEqual(device.calls, previous)
            self.assertEqual(len(list(Path(directory).glob('devices/*/unlock-attempt.json'))), 1)

    def test_boot_change_before_unlock_prevents_write(self):
        with tempfile.TemporaryDirectory() as directory:
            device = FakeDevice(directory)
            with patch.object(o, 'lock_state', return_value=(1, 'old-boot')):
                with self.assertRaises(o.Stop):
                    o.unlock(device, True)
            self.assertFalse(any(c[0] == 'unlock-once' for c in device.calls))

    def test_malformed_success_response_is_not_accepted_or_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            device = FakeDevice(directory, {'returncode': 0, 'timed_out': False, 'stdout': 'null'})
            with patch.object(o, 'lock_state', return_value=(1, BOOT)):
                with self.assertRaises(o.Stop):
                    o.unlock(device, True)
            self.assertEqual(sum(c[0] == 'unlock-once' for c in device.calls), 1)

    def test_exclusive_journal_preserves_previous_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            record = Path(directory) / 'attempt.json'
            o.save(record, {'attempt': 1}, exclusive=True)
            with self.assertRaises(FileExistsError):
                o.save(record, {'attempt': 2}, exclusive=True)
            self.assertEqual(json.loads(record.read_text()), {'attempt': 1})

    def test_boot_image_layout_and_pinned_stock_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / 'init_boot.img'
            header = bytearray(4096)
            header[:8] = b'ANDROID!'
            struct.pack_into('<II', header, 8, 0, 1024)
            struct.pack_into('<I', header, 40, 4)
            with image.open('wb') as stream:
                stream.write(header)
                stream.truncate(o.PROFILE['init_boot_size'])
            o.validate_image(image)
            with self.assertRaises(o.Stop):
                o.validate_image(image, stock=True)
            for offset, value in ((8, 1), (12, 0), (12, o.PROFILE['init_boot_size']), (40, 3)):
                with self.subTest(offset=offset, value=value):
                    modified = bytearray(header)
                    struct.pack_into('<I', modified, offset, value)
                    with image.open('r+b') as stream:
                        stream.write(modified)
                    with self.assertRaises(o.Stop):
                        o.validate_image(image)


if __name__ == '__main__':
    unittest.main()
