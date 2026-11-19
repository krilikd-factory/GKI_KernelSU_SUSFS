#!/usr/bin/env bash
# Build the pinned NoMount CLI for both Android ABIs and reject wrong/dynamic output.
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "Usage: $0 <NoMount source directory> <output directory>" >&2
  exit 2
fi

expected_zig="0.17.1"
actual_zig="$(zig version)"
if [ "$actual_zig" != "$expected_zig" ]; then
  echo "NoMount requires tested Zig $expected_zig, got $actual_zig." >&2
  exit 1
fi

source_dir="$(cd "$1" && pwd)"
output_dir="$2"
source_file="$source_dir/userspace/src/nm.c"
if [ ! -f "$source_file" ]; then
  echo "NoMount userspace source is missing: $source_file" >&2
  exit 1
fi
mkdir -p "$output_dir"
output_dir="$(cd "$output_dir" && pwd)"

# nm.c includes libc/Linux headers and calls libc routines. An ABI-less target
# supplies neither the headers nor startup code; -nostdlib also suppresses the
# musl include paths. Use explicit musl ABIs and the normal libc main() startup.
(
  cd "$source_dir/userspace/src"
  zig cc -target aarch64-linux-musl -Oz -mcmodel=tiny -static \
    -ffreestanding -fno-unwind-tables -fno-ident -Wno-invalid-noreturn \
    nm.c -o "$output_dir/nm-arm64"
  zig cc -target arm-linux-musleabi -Oz -static -ffreestanding \
    -fno-unwind-tables -fno-ident -Wno-invalid-noreturn \
    -ffunction-sections -fdata-sections -Wl,--gc-sections \
    nm.c -o "$output_dir/nm-arm"
)

python3 - "$output_dir/nm-arm64" "$output_dir/nm-arm" <<'PY'
import hashlib
import os
import struct
import sys

expected = ((2, 183, "AArch64"), (1, 40, "ARM"))
for path, (elf_class, machine, label) in zip(sys.argv[1:], expected):
    data = open(path, "rb").read()
    if data[:4] != b"\x7fELF" or data[5] != 1:
        raise SystemExit(f"{label}: expected a little-endian ELF: {path}")
    if data[4] != elf_class:
        raise SystemExit(f"{label}: wrong ELF class in {path}")
    endian = "<"
    header_size = 64 if elf_class == 2 else 52
    fields = struct.unpack_from(endian + ("HHIQQQIHHHHHH" if elf_class == 2 else "HHI IIIIHHHHHH".replace(" ", "")), data, 16)
    elf_type, actual_machine = fields[0], fields[1]
    entry = fields[3]
    phoff = fields[4]
    phentsize, phnum = fields[8], fields[9]
    if elf_type != 2 or actual_machine != machine or entry == 0:
        raise SystemExit(f"{label}: wrong type/machine/entry in {path}")
    ph_format = endian + ("IIQQQQQQ" if elf_class == 2 else "IIIIIIII")
    for index in range(phnum):
        offset = phoff + index * phentsize
        if offset + struct.calcsize(ph_format) > len(data):
            raise SystemExit(f"{label}: truncated program header table")
        if struct.unpack_from(ph_format, data, offset)[0] == 2:  # PT_DYNAMIC
            raise SystemExit(f"{label}: dynamic dependencies are forbidden")
    if not os.access(path, os.X_OK):
        raise SystemExit(f"{label}: output is not executable")
    digest = hashlib.sha256(data).hexdigest()
    print(f"PASS {label}: static ELF, entry=0x{entry:x}, sha256={digest}")
PY
