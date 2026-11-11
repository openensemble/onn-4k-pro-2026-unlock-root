#!/usr/bin/env python3
"""Read inode ownership, modes and xattrs from the pinned, single-device EROFS image.

Directory records and inode layouts follow erofs-utils v1.7.1 erofs_fs.h.
Regular file contents are extracted separately with fsck.erofs.
"""
import base64
import json
import mmap
from pathlib import Path
import stat
import struct
import sys


class Metadata:
    def __init__(self, path):
        self.file = open(path, 'rb')
        self.data = mmap.mmap(self.file.fileno(), 0, access=mmap.ACCESS_READ)
        assert self.u32(1024) == 0xE0F5E1E2, 'not EROFS'
        self.bs = 1 << self.data[1036]
        assert self.bs == 4096, 'unexpected block size'
        self.root = self.u16(1038)
        self.meta = self.u32(1064) * self.bs
        self.xattr = self.u32(1068) * self.bs
        assert self.u16(1110) == 0, 'multiple devices unsupported'
        self.rows = {}

    def u16(self, p):
        return struct.unpack_from('<H', self.data, p)[0]

    def u32(self, p):
        return struct.unpack_from('<I', self.data, p)[0]

    def u64(self, p):
        return struct.unpack_from('<Q', self.data, p)[0]

    def attribute(self, p):
        length, index, size = struct.unpack_from('<BBH', self.data, p)
        prefixes = {1: 'user.', 2: 'system.posix_acl_access',
                    3: 'system.posix_acl_default', 4: 'trusted.', 6: 'security.'}
        assert index in prefixes, ('unsupported xattr index', index, p)
        name = prefixes[index] + self.data[p+4:p+4+length].decode('ascii')
        value = self.data[p+4+length:p+4+length+size]
        return name, base64.b64encode(value).decode(), (4 + length + size + 3) & ~3

    def inode(self, nid):
        address = self.meta + nid * 32
        fmt, count, mode = struct.unpack_from('<HHH', self.data, address)
        assert not fmt & ~15
        extended = fmt & 1
        header = 64 if extended else 32
        size = self.u64(address+8) if extended else self.u32(address+8)
        uid = self.u32(address+24) if extended else self.u16(address+24)
        gid = self.u32(address+28) if extended else self.u16(address+26)
        xsize = 12 + (count - 1) * 4 if count else 0
        attrs = {}
        if xsize:
            start = address + header
            shares = self.data[start+4]
            assert 12 + shares*4 <= xsize
            for i in range(shares):
                at = self.xattr + self.u32(start+12+i*4)*4
                name, value, _ = self.attribute(at)
                assert name not in attrs
                attrs[name] = value
            pos, end = start + 12 + shares*4, start + xsize
            while pos < end:
                if not any(self.data[pos:end]):
                    break
                name, value, used = self.attribute(pos)
                assert pos + used <= end and name not in attrs
                attrs[name] = value
                pos += used
        return {'nid': nid, 'mode': mode, 'uid': uid, 'gid': gid, 'size': size,
                'layout': (fmt >> 1) & 7, 'raw_block': self.u32(address+16),
                'inline': address+header+xsize, 'xattrs': attrs}

    def walk(self, path='', nid=None):
        inode = self.inode(self.root if nid is None else nid)
        assert path not in self.rows
        self.rows[path] = inode
        if not stat.S_ISDIR(inode['mode']):
            return
        assert inode['layout'] in (0, 2), 'unsupported directory layout'
        for logical in range(0, inode['size'], self.bs):
            size = min(self.bs, inode['size']-logical)
            offset = (inode['inline'] if inode['layout'] == 2 and size < self.bs
                      else inode['raw_block'] * self.bs + logical)
            block = self.data[offset:offset+size]
            first_name = struct.unpack_from('<H', block, 8)[0]
            assert first_name % 12 == 0 and 12 <= first_name <= size
            entries = [struct.unpack_from('<QHBB', block, i)
                       for i in range(0, first_name, 12)]
            for i, (child, name_at, kind, reserved) in enumerate(entries):
                name_end = entries[i+1][1] if i+1 < len(entries) else size
                assert first_name <= name_at < name_end <= size and reserved == 0
                name = block[name_at:name_end].rstrip(b'\0').decode('utf-8')
                assert name and '/' not in name and '\0' not in name
                if name in ('.', '..'):
                    continue
                self.walk(path + '/' + name if path else name, child)


if __name__ == '__main__':
    reader = Metadata(sys.argv[1])
    reader.walk()
    Path(sys.argv[2]).write_text(json.dumps(reader.rows, indent=2, sort_keys=True)+'\n')
    print(json.dumps({'paths': len(reader.rows), 'unique_inodes': len({r['nid'] for r in reader.rows.values()}),
                      'xattr_names': sorted({k for r in reader.rows.values() for k in r['xattrs']}),
                      'uids': sorted({r['uid'] for r in reader.rows.values()}),
                      'gids': sorted({r['gid'] for r in reader.rows.values()})}, indent=2))
