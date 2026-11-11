#!/usr/bin/env python3
"""Extract selected full-payload partitions offline; never opens device nodes.

Wire fields follow AOSP system/update_engine/update_metadata.proto. This small
reader deliberately rejects delta operations rather than producing bad images.
Payload, operation, and resulting partition SHA-256 values must all match.
These integrity checks do not authenticate a vendor signing key.
"""
import argparse
import base64
import bz2
import hashlib
import json
import lzma
from pathlib import Path
import re
import struct
import zipfile


def varint(data, offset):
    value = 0
    for shift in range(0, 70, 7):
        if offset >= len(data):
            raise ValueError("Truncated protobuf varint")
        byte = data[offset]
        offset += 1
        value |= (byte & 127) << shift
        if not byte & 128:
            return value, offset
    raise ValueError("Oversized protobuf varint")


def fields(data):
    result = {}
    pos = 0
    while pos < len(data):
        tag, pos = varint(data, pos)
        number, wire = tag >> 3, tag & 7
        if not number:
            raise ValueError("Invalid protobuf field")
        if wire == 0:
            value, pos = varint(data, pos)
        elif wire in (1, 2, 5):
            if wire == 2:
                size, pos = varint(data, pos)
            else:
                size = {1: 8, 5: 4}[wire]
            if pos + size > len(data):
                raise ValueError("Truncated protobuf field")
            value = data[pos:pos + size]
            pos += size
        else:
            raise ValueError(f"Unsupported protobuf wire type {wire}")
        result.setdefault(number, []).append(value)
    return result


def one(message, key, default=None):
    values = message.get(key, [default])
    if len(values) != 1:
        raise ValueError(f"Duplicate scalar field {key}")
    return values[0]


def partition_info(raw):
    if raw is None:
        return None
    message = fields(raw)
    return {"size": one(message, 1), "sha256": one(message, 2).hex()}


def payload_location(archive, info):
    if info.compress_type != zipfile.ZIP_STORED:
        raise ValueError("Only an uncompressed payload.bin ZIP member is supported")
    with archive.open("rb") as handle:
        handle.seek(info.header_offset)
        header = handle.read(30)
        if len(header) != 30 or header[:4] != b"PK\x03\x04":
            raise ValueError("Invalid ZIP local header")
        name_size, extra_size = struct.unpack_from("<HH", header, 26)
        if handle.read(name_size) != b"payload.bin":
            raise ValueError("Unexpected local member name")
    return info.header_offset + 30 + name_size + extra_size


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--partitions", nargs="+", default=["bootloader"])
    args = parser.parse_args()
    archive = args.archive.resolve()
    if not archive.is_file():
        raise ValueError("Input must be a regular archive file")
    args.output.mkdir(parents=True, exist_ok=True)
    for name in args.partitions:
        if not re.fullmatch(r"[a-zA-Z0-9_]+", name):
            raise ValueError("Invalid partition name")
        if (args.output / (name + ".img")).exists():
            raise ValueError(f"Refusing to overwrite {name}.img")
    with zipfile.ZipFile(archive) as zipped:
        info = zipped.getinfo("payload.bin")
        payload_start = payload_location(archive, info)
        props_raw = zipped.read("payload_properties.txt")
        props = dict(line.split("=", 1) for line in props_raw.decode().splitlines())
        metadata = zipped.read("META-INF/com/android/metadata")
        (args.output / "ota-metadata.txt").write_bytes(metadata)
        (args.output / "payload_properties.txt").write_bytes(props_raw)
        digest = hashlib.sha256()
        with zipped.open(info) as payload:
            while chunk := payload.read(4 * 1024 * 1024):
                digest.update(chunk)
        if digest.digest() != base64.b64decode(props["FILE_HASH"]):
            raise ValueError("Payload SHA-256 mismatch")
        if info.file_size != int(props["FILE_SIZE"]):
            raise ValueError("Payload size mismatch")
    with archive.open("rb") as handle:
        handle.seek(payload_start)
        header = handle.read(24)
        magic, major, manifest_size, signature_size = struct.unpack(">4sQQI", header)
        if magic != b"CrAU" or major != 2 or manifest_size > 16 * 1024 * 1024:
            raise ValueError("Unsupported payload header")
        manifest_raw = handle.read(manifest_size)
        metadata_end = payload_start + 24 + manifest_size
        handle.seek(payload_start)
        checked_metadata = handle.read(int(props["METADATA_SIZE"]))
        if hashlib.sha256(checked_metadata).digest() != base64.b64decode(props["METADATA_HASH"]):
            raise ValueError("Metadata SHA-256 mismatch")
        (args.output / "payload-header-manifest.bin").write_bytes(header + manifest_raw)
        manifest = fields(manifest_raw)
        block = one(manifest, 3, 4096)
        if one(manifest, 12, 0) != 0:
            raise ValueError("Delta payload: a verified source image is required")
        data_start = metadata_end + signature_size
        summary = {
            "archive": str(archive), "payload_bytes": info.file_size,
            "payload_sha256": digest.hexdigest(), "payload_hash_verified": True,
            "metadata_hash_verified": True, "vendor_signature_verified": False,
            "major_version": major, "minor_version": 0, "block_size": block,
            "manifest_size": manifest_size, "metadata_signature_size": signature_size,
            "max_timestamp": one(manifest, 14), "partitions": [], "extracted": [],
        }
        for raw in manifest.get(13, []):
            partition = fields(raw)
            name = one(partition, 1).decode()
            operations = [fields(op) for op in partition.get(8, [])]
            new_info = partition_info(one(partition, 7))
            summary["partitions"].append({
                "name": name, "new": new_info,
                "old": partition_info(one(partition, 6)),
                "operation_types": sorted({one(op, 1) for op in operations}),
                "operation_count": len(operations),
            })
            if name not in args.partitions:
                continue
            size = new_info["size"]
            if not 0 < size <= 2 * 1024 * 1024 * 1024 or size % block:
                raise ValueError(f"Unsupported extraction size for {name}: {size}")
            image = bytearray(size)
            coverage = bytearray(size // block)
            for operation in operations:
                kind = one(operation, 1)
                if kind not in (0, 1, 6, 8):
                    raise ValueError(f"Unsupported operation {kind} in {name}")
                extents = [fields(raw) for raw in operation.get(6, [])]
                expected = sum(one(e, 2) for e in extents) * block
                if kind == 6:
                    blob = bytes(expected)
                else:
                    offset = one(operation, 2, 0)
                    length = one(operation, 3, 0)
                    if length <= 0 or data_start + offset + length > payload_start + info.file_size:
                        raise ValueError("Invalid operation data bounds")
                    handle.seek(data_start + offset)
                    compressed = handle.read(length)
                    expected_hash = one(operation, 8)
                    if expected_hash is None or hashlib.sha256(compressed).digest() != expected_hash:
                        raise ValueError(f"Operation SHA-256 mismatch for {name}")
                    blob = {0: lambda x: x, 1: bz2.decompress, 8: lzma.decompress}[kind](compressed)
                if len(blob) > expected:
                    raise ValueError("Decoded data exceeds destination extents")
                blob += bytes(expected - len(blob))
                pos = 0
                for extent in extents:
                    start, count = one(extent, 1), one(extent, 2)
                    if start + count > len(coverage) or any(coverage[start:start + count]):
                        raise ValueError("Out-of-bounds or overlapping destination extent")
                    coverage[start:start + count] = bytes([1]) * count
                    length = count * block
                    image[start * block:(start + count) * block] = blob[pos:pos + length]
                    pos += length
            if not all(coverage):
                raise ValueError(f"Incomplete block coverage for {name}")
            actual_hash = hashlib.sha256(image).hexdigest()
            if actual_hash != new_info["sha256"]:
                raise ValueError(f"Final partition SHA-256 mismatch for {name}")
            output = args.output / (name + ".img")
            with output.open("xb") as target:
                target.write(image)
            record = {"name": name, "file": str(output.resolve()), "bytes": size,
                      "sha256": actual_hash, "operation_hashes_verified": True,
                      "partition_hash_verified": True, "all_blocks_covered": True}
            summary["extracted"].append(record)
            print(json.dumps(record), flush=True)
        missing = set(args.partitions) - {x["name"] for x in summary["extracted"]}
        if missing:
            raise ValueError(f"Missing requested partitions: {sorted(missing)}")
    (args.output / "extraction-receipt.json").write_text(json.dumps(summary, indent=2) + "\n")


if __name__ == "__main__":
    main()
