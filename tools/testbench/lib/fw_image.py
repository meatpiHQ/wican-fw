#!/usr/bin/env python3
"""An ESP32-S3 application image's descriptor, read and patched for the bench
(2026-10-18, internal/TASK_fw_update.md 9.2).

The firmware update bench needs several images that differ only in what
their app descriptor says (a newer version, a foreign project name) and it
must not need a second build: this patches the 32-byte `version` (image
offset 48) and `project_name` (offset 80) fields of a built image, then
recomputes what the bootloader and esp_ota_end check: the XOR checksum byte
that closes the segments and the SHA-256 appended after it.

    python tools/testbench/lib/fw_image.py info build/wican-fw_obd_pro.bin
    python tools/testbench/lib/fw_image.py patch IN OUT --version 6.1.0-alfa.90
    python tools/testbench/lib/fw_image.py patch IN OUT --project espnetlink-fw_bench

`info` prints the descriptor and whether the checksum and the digest hold
(`esptool image-info` agrees). As a library: `info(data)`, `patch(data,
version=..., project=...)`, `verify(data)`, `sha256_hex(data)`.
"""
import argparse
import hashlib
import json
import struct
import sys

IMAGE_MAGIC = 0xE9
DESC_MAGIC = 0xABCD5432
HEADER_LEN = 24
SEG_HDR_LEN = 8
DESC_OFF = HEADER_LEN + SEG_HDR_LEN   # 32: the descriptor opens segment 0
VERSION_OFF = DESC_OFF + 16           # 48
PROJECT_OFF = DESC_OFF + 48           # 80
CHECKSUM_SEED = 0xEF


class ImageError(ValueError):
    pass


def _layout(data):
    """(segments as [(hdr_off, data_off, data_len)], checksum_off, hash_appended)."""
    if len(data) < HEADER_LEN or data[0] != IMAGE_MAGIC:
        raise ImageError("not an application image (magic)")
    seg_count = data[1]
    hash_appended = data[23] == 1
    pos = HEADER_LEN
    segs = []
    for _ in range(seg_count):
        if pos + SEG_HDR_LEN > len(data):
            raise ImageError("segment header past the end")
        _addr, length = struct.unpack_from("<II", data, pos)
        segs.append((pos, pos + SEG_HDR_LEN, length))
        pos += SEG_HDR_LEN + length
        if pos > len(data):
            raise ImageError("segment data past the end")
    # the checksum byte closes a 16-byte aligned block
    checksum_off = (pos + 16) // 16 * 16 - 1 if (pos % 16) != 15 else pos
    if checksum_off >= len(data):
        raise ImageError("checksum position past the end")
    return segs, checksum_off, hash_appended


def _checksum(data, segs):
    c = CHECKSUM_SEED
    for _hdr, off, length in segs:
        for b in data[off:off + length]:
            c ^= b
    return c


def _field(data, off):
    raw = data[off:off + 32]
    return raw.split(b"\0", 1)[0].decode("utf-8", errors="replace")


def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def info(data):
    """The descriptor and the integrity verdicts of one image."""
    segs, csum_off, hash_appended = _layout(data)
    chip_id = struct.unpack_from("<H", data, 12)[0]
    desc_magic = struct.unpack_from("<I", data, DESC_OFF)[0]
    out = {
        "size": len(data), "segments": len(segs), "chip_id": chip_id,
        "has_descriptor": desc_magic == DESC_MAGIC,
        "version": _field(data, VERSION_OFF), "project_name": _field(data, PROJECT_OFF),
        "idf_ver": _field(data, DESC_OFF + 112),
        "hash_appended": hash_appended, "checksum_off": csum_off,
        "sha256": sha256_hex(data),
    }
    out["checksum_ok"] = data[csum_off] == _checksum(data, segs)
    if hash_appended:
        body = data[:csum_off + 1]
        out["digest_ok"] = data[csum_off + 1:csum_off + 33] == hashlib.sha256(body).digest()
        out["trailer_ok"] = len(data) == csum_off + 33
    else:
        out["digest_ok"] = None
        out["trailer_ok"] = len(data) == csum_off + 1
    return out


def verify(data):
    i = info(data)
    return bool(i["checksum_ok"] and (i["digest_ok"] is None or i["digest_ok"]) and i["trailer_ok"])


def _put_field(buf, off, text):
    raw = text.encode("utf-8")
    if len(raw) > 31:
        raise ImageError("field longer than 31 bytes: " + text)
    buf[off:off + 32] = raw + b"\0" * (32 - len(raw))


def patch(data, version=None, project=None):
    """A copy of @p data with the descriptor fields replaced and the
    checksum and the appended digest recomputed."""
    segs, csum_off, hash_appended = _layout(data)
    if struct.unpack_from("<I", data, DESC_OFF)[0] != DESC_MAGIC:
        raise ImageError("no application descriptor at offset 32")
    buf = bytearray(data)
    if version is not None:
        _put_field(buf, VERSION_OFF, version)
    if project is not None:
        _put_field(buf, PROJECT_OFF, project)
    buf[csum_off] = _checksum(buf, segs)
    if hash_appended:
        buf[csum_off + 1:csum_off + 33] = hashlib.sha256(bytes(buf[:csum_off + 1])).digest()
    return bytes(buf)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    pi = sub.add_parser("info", help="print the descriptor and the integrity verdicts")
    pi.add_argument("image")
    pp = sub.add_parser("patch", help="write a copy with another version / project name")
    pp.add_argument("image")
    pp.add_argument("out")
    pp.add_argument("--version")
    pp.add_argument("--project")
    a = ap.parse_args()
    with open(a.image, "rb") as f:
        data = f.read()
    if a.cmd == "info":
        print(json.dumps(info(data), indent=1))
        return 0 if verify(data) else 1
    out = patch(data, a.version, a.project)
    with open(a.out, "wb") as f:
        f.write(out)
    i = info(out)
    print(json.dumps({"out": a.out, "version": i["version"], "project_name": i["project_name"],
                      "size": i["size"], "sha256": i["sha256"], "ok": verify(out)}, indent=1))
    return 0 if verify(out) else 1


if __name__ == "__main__":
    sys.exit(main())
