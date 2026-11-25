"""Pure-Python GNU gettext .po -> .mo compiler (works on Windows/Linux without msgfmt)."""

import array
import ast
from pathlib import Path
import struct


def parse_po(po_path: Path) -> dict[bytes, bytes]:
    messages: dict[str, str] = {}
    section = None
    msgid_parts: list[str] = []
    msgstr_parts: list[str] = []

    def flush():
        if section is not None:
            key = "".join(msgid_parts)
            val = "".join(msgstr_parts)
            if val:
                messages[key] = val

    for raw_line in po_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            if not line:
                flush()
                section = None
                msgid_parts = []
                msgstr_parts = []
            continue
        if line.startswith("msgid "):
            flush()
            section = "id"
            msgid_parts = [ast.literal_eval(line[6:].strip())]
            msgstr_parts = []
        elif line.startswith("msgstr "):
            section = "str"
            msgstr_parts = [ast.literal_eval(line[7:].strip())]
        elif line.startswith('"'):
            piece = ast.literal_eval(line)
            if section == "id":
                msgid_parts.append(piece)
            elif section == "str":
                msgstr_parts.append(piece)

    flush()
    return {k.encode("utf-8"): v.encode("utf-8") for k, v in messages.items()}


def write_mo(messages: dict[bytes, bytes], mo_path: Path) -> None:
    keys = sorted(messages.keys())
    offsets = []
    ids = b""
    strs = b""
    for k in keys:
        offsets.append((len(ids), len(k), len(strs), len(messages[k])))
        ids += k + b"\x00"
        strs += messages[k] + b"\x00"

    keystart = 7 * 4 + 16 * len(keys)
    valuestart = keystart + len(ids)
    koffsets = []
    voffsets = []
    for o1, l1, o2, l2 in offsets:
        koffsets.extend((l1, o1 + keystart))
        voffsets.extend((l2, o2 + valuestart))

    header = struct.pack(
        "Iiiiiii",
        0x950412DE,
        0,
        len(keys),
        7 * 4,
        7 * 4 + len(keys) * 8,
        0,
        0,
    )
    mo_path.parent.mkdir(parents=True, exist_ok=True)
    mo_path.write_bytes(
        header + array.array("i", koffsets).tobytes() + array.array("i", voffsets).tobytes() + ids + strs
    )


def main() -> None:
    base = Path(__file__).resolve().parent.parent / "locale" / "ar" / "LC_MESSAGES"
    po_file = base / "django.po"
    mo_file = base / "django.mo"
    catalog = parse_po(po_file)
    write_mo(catalog, mo_file)
    print(f"Compiled {len(catalog)} entries -> {mo_file}")


if __name__ == "__main__":
    main()
