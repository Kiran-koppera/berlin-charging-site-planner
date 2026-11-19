"""Write a VBA project (vbaProject.bin) and a macro-enabled workbook (.xlsm) without Excel.

openpyxl cannot create VBA. This module builds the binary VBA project from plain-text
modules, following Microsoft's published specifications:

* [MS-OVBA] Office VBA File Format: PROJECT and PROJECTwm streams, the `dir` stream,
  module streams, the compression algorithm and the data "encryption" of CMG/DPB/GC.
* [MS-CFB] Compound File Binary format: the OLE container that holds the streams.

Module streams contain compressed source only (no compiled p-code, `_VBA_PROJECT` version
0xFFFF), so Excel compiles the code when the workbook is opened. The workbook is an
openpyxl .xlsx turned into .xlsm: macro content type, vbaProject relationship and code names.
"""

from __future__ import annotations

import io
import random
import re
import struct
import uuid
import zipfile
from pathlib import Path

CODEPAGE = 1252
BASE_WORKBOOK = "0{00020819-0000-0000-C000-000000000046}"
BASE_SHEET = "0{00020820-0000-0000-C000-000000000046}"
STDOLE = "*\\G{00020430-0000-0000-C000-000000000046}#2.0#0#C:\\Windows\\System32\\stdole2.tlb#OLE Automation"


# --------------------------------------------------------------------------------------------
# [MS-OVBA] 2.4.1 compression
# --------------------------------------------------------------------------------------------
def _copy_token_help(diff: int) -> tuple[int, int]:
    bits = max((diff - 1).bit_length(), 4)
    length_mask = 0xFFFF >> bits
    return bits, length_mask + 3  # bit count, maximum length


def compress(data: bytes) -> bytes:
    out = bytearray(b"\x01")
    for start in range(0, len(data), 4096):
        chunk = data[start:start + 4096]
        body = bytearray()
        pos = 0
        while pos < len(chunk):
            flag_at = len(body)
            body.append(0)
            flags = 0
            for bit in range(8):
                if pos >= len(chunk):
                    break
                best_len, best_off = 0, 0
                if pos > 0:
                    bits, max_len = _copy_token_help(pos)
                    for cand in range(pos - 1, -1, -1):
                        length = 0
                        while pos + length < len(chunk) and length < max_len and chunk[cand + length] == chunk[pos + length]:
                            length += 1
                        if length > best_len:
                            best_len, best_off = length, pos - cand
                            if length == max_len:
                                break
                if best_len >= 3:
                    bits, _ = _copy_token_help(pos)
                    token = ((best_off - 1) << (16 - bits)) | (best_len - 3)
                    body += struct.pack("<H", token)
                    flags |= 1 << bit
                    pos += best_len
                else:
                    body.append(chunk[pos])
                    pos += 1
            body[flag_at] = flags
        if len(body) > 4096:  # incompressible: store raw (only valid for a full 4096-byte chunk)
            if len(chunk) != 4096:
                raise ValueError("final chunk does not compress")
            out += struct.pack("<H", 0x3000 | 4095) + chunk
        else:
            out += struct.pack("<H", 0x8000 | 0x3000 | (len(body) + 2 - 3)) + body
    return bytes(out)


def decompress(data: bytes) -> bytes:
    if data[0] != 1:
        raise ValueError("bad signature byte")
    out = bytearray()
    i = 1
    while i < len(data):
        header = struct.unpack_from("<H", data, i)[0]
        size = (header & 0x0FFF) + 3
        chunk_end = i + size
        i += 2
        start = len(out)
        if not header & 0x8000:
            out += data[i:i + 4096]
            i += 4096
            continue
        while i < chunk_end:
            flags = data[i]
            i += 1
            for bit in range(8):
                if i >= chunk_end:
                    break
                if flags >> bit & 1:
                    token = struct.unpack_from("<H", data, i)[0]
                    i += 2
                    bits, _ = _copy_token_help(len(out) - start)
                    length = (token & (0xFFFF >> bits)) + 3
                    offset = (token >> (16 - bits)) + 1
                    for _ in range(length):
                        out.append(out[-offset])
                else:
                    out.append(data[i])
                    i += 1
    return bytes(out)


# --------------------------------------------------------------------------------------------
# [MS-OVBA] 2.4.3 data encryption (obfuscation of CMG, DPB and GC)
# --------------------------------------------------------------------------------------------
def encrypt(data: bytes, project_id: str, seed: int | None = None) -> str:
    rnd = random.Random(seed)
    s = rnd.randrange(256)
    version, key = 2, sum(project_id.encode("ascii")) & 0xFF
    out = [s, s ^ version, s ^ key]
    unenc1, enc1, enc2 = key, out[2], out[1]

    def push(b: int) -> None:
        nonlocal unenc1, enc1, enc2
        e = b ^ ((enc2 + unenc1) & 0xFF)
        out.append(e)
        enc2, enc1, unenc1 = enc1, e, b

    for _ in range((s & 6) // 2):
        push(rnd.randrange(256))
    for b in struct.pack("<I", len(data)) + data:
        push(b)
    return bytes(out).hex().upper()


def decrypt(hexdata: str) -> bytes:
    d = bytes.fromhex(hexdata)
    s, version_enc, key_enc = d[0], d[1], d[2]
    if s ^ version_enc != 2:
        raise ValueError("bad version")
    unenc1, enc1, enc2 = s ^ key_enc, key_enc, version_enc
    plain = []
    for e in d[3:]:
        b = e ^ ((enc2 + unenc1) & 0xFF)
        plain.append(b)
        enc2, enc1, unenc1 = enc1, e, b
    ignored = (s & 6) // 2
    rest = bytes(plain[ignored:])
    n = struct.unpack_from("<I", rest)[0]
    return rest[4:4 + n]


# --------------------------------------------------------------------------------------------
# VBA project streams
# --------------------------------------------------------------------------------------------
def document_module(name: str, base: str) -> str:
    return "\r\n".join([
        f'Attribute VB_Name = "{name}"', f'Attribute VB_Base = "{base}"', "Attribute VB_GlobalNameSpace = False",
        "Attribute VB_Creatable = False", "Attribute VB_PredeclaredId = True", "Attribute VB_Exposed = True",
        "Attribute VB_TemplateDerived = False", "Attribute VB_Customizable = True", ""])


def _rec(rid: int, payload: bytes) -> bytes:
    return struct.pack("<HI", rid, len(payload)) + payload


def _mbcs(s: str) -> bytes:
    return s.encode("cp1252")


def _u16(s: str) -> bytes:
    return s.encode("utf-16-le")


def dir_stream(modules: list[tuple[str, str]]) -> bytes:
    """modules: (name, kind) with kind 'document' or 'standard'."""
    d = bytearray()
    d += _rec(0x0001, struct.pack("<I", 1))  # SysKind: 32-bit Windows
    d += _rec(0x0002, struct.pack("<I", 0x0409))  # LCID
    d += _rec(0x0014, struct.pack("<I", 0x0409))  # LCID invoke
    d += _rec(0x0003, struct.pack("<H", CODEPAGE))
    d += _rec(0x0004, _mbcs("VBAProject"))
    d += _rec(0x0005, b"") + _rec(0x0040, b"")  # doc string
    d += _rec(0x0006, b"") + _rec(0x003D, b"")  # help file
    d += _rec(0x0007, struct.pack("<I", 0))  # help context
    d += _rec(0x0008, struct.pack("<I", 0))  # lib flags
    d += struct.pack("<HIIH", 0x0009, 4, 1, 0)  # version: reserved size 4, major, minor
    d += _rec(0x000C, b"") + _rec(0x003C, b"")  # constants
    # reference to OLE Automation (stdole)
    d += _rec(0x0016, _mbcs("stdole")) + _rec(0x003E, _u16("stdole"))
    libid = _mbcs(STDOLE)
    d += _rec(0x000D, struct.pack("<I", len(libid)) + libid + struct.pack("<IH", 0, 0))
    d += _rec(0x000F, struct.pack("<H", len(modules)))
    d += _rec(0x0013, struct.pack("<H", 0xFFFF))
    for name, kind in modules:
        d += _rec(0x0019, _mbcs(name)) + _rec(0x0047, _u16(name))
        d += _rec(0x001A, _mbcs(name)) + _rec(0x0032, _u16(name))
        d += _rec(0x001C, b"") + _rec(0x0048, b"")
        d += _rec(0x0031, struct.pack("<I", 0))  # text offset: source starts at byte 0
        d += _rec(0x001E, struct.pack("<I", 0))
        d += _rec(0x002C, struct.pack("<H", 0xFFFF))
        d += struct.pack("<HI", 0x0022 if kind == "document" else 0x0021, 0)
        d += struct.pack("<HI", 0x002B, 0)
    d += struct.pack("<HI", 0x0010, 0)
    return bytes(d)


def project_stream(modules: list[tuple[str, str]], project_id: str) -> bytes:
    lines = [f'ID="{project_id}"']
    for name, kind in modules:
        lines.append(f"Document={name}/&H00000000" if kind == "document" else f"Module={name}")
    cmg = encrypt(struct.pack("<I", 0), project_id, 1)  # not protected
    dpb = encrypt(bytes([0]), project_id, 2)  # no password
    gc = encrypt(bytes([0xFF]), project_id, 3)  # visible
    lines += ['Name="VBAProject"', 'HelpContextID="0"', 'VersionCompatible32="393222000"',
              f'CMG="{cmg}"', f'DPB="{dpb}"', f'GC="{gc}"',
              "", "[Host Extender Info]", "&H00000001={3832D640-CF90-11CF-8E43-00A0C911005A};VBE;&H00000000",
              "", "[Workspace]"]
    lines += [f"{name}=0, 0, 0, 0, C" for name, _ in modules]
    return ("\r\n".join(lines) + "\r\n").encode("cp1252")


def projectwm_stream(modules: list[tuple[str, str]]) -> bytes:
    out = bytearray()
    for name, _ in modules:
        out += _mbcs(name) + b"\x00" + _u16(name) + b"\x00\x00"
    return bytes(out + b"\x00\x00")


def build_vba_project(standard: dict[str, str], sheet_codenames: list[str], project_id: str | None = None) -> bytes:
    """standard: {module name: source}. Document modules are generated for ThisWorkbook and sheets."""
    project_id = project_id or "{" + str(uuid.uuid5(uuid.NAMESPACE_URL, "berlin-charging-site-planner")).upper() + "}"
    docs = [("ThisWorkbook", document_module("ThisWorkbook", BASE_WORKBOOK))]
    docs += [(n, document_module(n, BASE_SHEET)) for n in sheet_codenames]
    mods = [(n, "document") for n, _ in docs] + [(n, "standard") for n in standard]
    sources = dict(docs) | standard
    streams = {
        "PROJECT": project_stream(mods, project_id),
        "PROJECTwm": projectwm_stream(mods),
        "VBA/_VBA_PROJECT": struct.pack("<HHBH", 0x61CC, 0xFFFF, 0x00, 0x0000),
        "VBA/dir": compress(dir_stream(mods)),
    }
    for name, src in sources.items():
        text = re.sub(r"\r?\n", "\r\n", src)
        streams[f"VBA/{name}"] = compress(text.encode("cp1252"))
    return write_cfb(streams)


# --------------------------------------------------------------------------------------------
# [MS-CFB] compound file writer (version 3, 512-byte sectors, mini stream for small streams)
# --------------------------------------------------------------------------------------------
FREESECT, ENDOFCHAIN, FATSECT, NOSTREAM = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD, 0xFFFFFFFF
SECTOR, MINI, CUTOFF = 512, 64, 4096


class _Node:
    def __init__(self, name: str, kind: int, data: bytes = b""):
        self.name, self.kind, self.data = name, kind, data
        self.children: list[_Node] = []
        self.left = self.right = self.child = NOSTREAM
        self.color = 1  # black
        self.start, self.size, self.sid = ENDOFCHAIN, 0, 0


def _cfb_key(n: _Node):
    return (len(n.name), n.name.upper())


def _balance(nodes: list[_Node], depth: int, out: list) -> int:
    """Balanced binary tree; nodes on an incomplete deepest level are red, so every path has
    the same number of black nodes (a valid red-black tree)."""
    if not nodes:
        return NOSTREAM
    mid = len(nodes) // 2
    n = nodes[mid]
    out.append((n, depth))
    n.left = _balance(nodes[:mid], depth + 1, out)
    n.right = _balance(nodes[mid + 1:], depth + 1, out)
    return n.sid


def write_cfb(streams: dict[str, bytes]) -> bytes:
    root = _Node("Root Entry", 5)
    storages: dict[str, _Node] = {"": root}
    for path, data in streams.items():
        parts = path.split("/")
        parent = root
        for i, part in enumerate(parts[:-1]):
            key = "/".join(parts[:i + 1])
            if key not in storages:
                storages[key] = _Node(part, 1)
                parent.children.append(storages[key])
            parent = storages[key]
        parent.children.append(_Node(parts[-1], 2, data))

    entries: list[_Node] = []

    def number(n: _Node) -> None:
        n.sid = len(entries)
        entries.append(n)
        for c in n.children:
            number(c)

    number(root)

    def link(n: _Node) -> None:
        if not n.children:
            return
        placed: list = []
        n.child = _balance(sorted(n.children, key=_cfb_key), 0, placed)
        maxd = max(d for _, d in placed)
        full = sum(1 for _, d in placed if d == maxd) == 2 ** maxd
        for c, d in placed:
            c.color = 0 if (d == maxd and maxd > 0 and not full) else 1
        for c in n.children:
            link(c)

    link(root)

    # mini stream
    mini = bytearray()
    minifat: list[int] = []
    big: list[_Node] = []
    for e in entries:
        if e.kind != 2:
            continue
        e.size = len(e.data)
        if e.size == 0:
            e.start = ENDOFCHAIN
        elif e.size < CUTOFF:
            n = -(-e.size // MINI)
            e.start = len(minifat)
            minifat += list(range(e.start + 1, e.start + n)) + [ENDOFCHAIN]
            mini += e.data + b"\x00" * (n * MINI - e.size)
        else:
            big.append(e)

    dir_sectors = -(-len(entries) // 4)
    minifat_sectors = -(-len(minifat) * 4 // SECTOR) if minifat else 0
    mini_sectors = -(-len(mini) // SECTOR) if mini else 0
    big_sectors = sum(-(-e.size // SECTOR) for e in big)
    content = dir_sectors + minifat_sectors + mini_sectors + big_sectors
    fat_sectors = 1
    while (fat_sectors + content) > fat_sectors * (SECTOR // 4):
        fat_sectors += 1
    if fat_sectors > 109:
        raise ValueError("file too large for this writer")

    fat: list[int] = [FATSECT] * fat_sectors
    body = bytearray()

    def alloc(data: bytes, nsec: int) -> int:
        if nsec == 0:
            return ENDOFCHAIN
        first = len(fat)
        fat.extend(list(range(first + 1, first + nsec)) + [ENDOFCHAIN])
        body.extend(data + b"\x00" * (nsec * SECTOR - len(data)))
        return first

    # directory placeholder, filled after start sectors are known
    dir_start = len(fat)
    fat.extend(list(range(dir_start + 1, dir_start + dir_sectors)) + [ENDOFCHAIN])
    body.extend(b"\x00" * dir_sectors * SECTOR)
    minifat_start = alloc(b"".join(struct.pack("<I", x) for x in minifat)
                          + struct.pack("<I", FREESECT) * (minifat_sectors * 128 - len(minifat)), minifat_sectors)
    root.start = alloc(bytes(mini), mini_sectors)
    root.size = len(mini)
    for e in big:
        e.start = alloc(e.data, -(-e.size // SECTOR))
    fat += [FREESECT] * (fat_sectors * 128 - len(fat))

    d = bytearray()
    for e in entries:
        name = e.name.encode("utf-16-le") + b"\x00\x00"
        d += name + b"\x00" * (64 - len(name))
        d += struct.pack("<HBBIII", len(name), e.kind, e.color, e.left, e.right, e.child)
        d += b"\x00" * 16 + struct.pack("<I", 0) + b"\x00" * 16
        d += struct.pack("<IQ", e.start if e.kind != 1 else 0, e.size)
    for _ in range(dir_sectors * 4 - len(entries)):
        d += b"\x00" * 64 + struct.pack("<HBBIII", 0, 0, 0, NOSTREAM, NOSTREAM, NOSTREAM) + b"\x00" * 36 + struct.pack("<IQ", 0, 0)
    body[0:len(d)] = d

    header = bytearray()
    header += bytes.fromhex("D0CF11E0A1B11AE1") + b"\x00" * 16
    header += struct.pack("<HHHHH", 0x003E, 0x0003, 0xFFFE, 9, 6) + b"\x00" * 6
    header += struct.pack("<IIIIIIIII", 0, fat_sectors, dir_start, 0, CUTOFF,
                          minifat_start if minifat_sectors else ENDOFCHAIN, minifat_sectors, ENDOFCHAIN, 0)
    header += b"".join(struct.pack("<I", i) for i in range(fat_sectors))
    header += struct.pack("<I", FREESECT) * (109 - fat_sectors)
    fat_bytes = b"".join(struct.pack("<I", x) for x in fat)
    return bytes(header) + fat_bytes + bytes(body)


# --------------------------------------------------------------------------------------------
# .xlsx -> .xlsm
# --------------------------------------------------------------------------------------------
VBA_REL = "http://schemas.microsoft.com/office/2006/relationships/vbaProject"
VBA_RELATIONSHIP = f'<Relationship Id="rIdVBA1" Type="{VBA_REL}" Target="vbaProject.bin"/>'
BIN_DEFAULT = '<Default Extension="bin" ContentType="application/vnd.ms-office.vbaProject"/>'


def make_xlsm(xlsx: Path, xlsm: Path, vba_bin: bytes) -> None:
    """Turn an openpyxl workbook (with code names set) into a macro-enabled workbook."""
    src = zipfile.ZipFile(xlsx)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "[Content_Types].xml":
                t = data.decode()
                t = t.replace("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
                              "application/vnd.ms-excel.sheet.macroEnabled.main+xml")
                if 'Extension="bin"' not in t:
                    t = t.replace("<Default ", BIN_DEFAULT + "<Default ", 1)
                data = t.encode()
            elif item.filename == "xl/_rels/workbook.xml.rels":
                t = data.decode()
                t = t.replace("</Relationships>", VBA_RELATIONSHIP + "</Relationships>")
                data = t.encode()
            out.writestr(item, data)
        out.writestr("xl/vbaProject.bin", vba_bin)
    xlsm.write_bytes(buf.getvalue())
