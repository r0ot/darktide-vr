"""Find the two deferred-skinning assert guards in a Darktide.exe.

The VR mod renders the world twice per engine frame, and Darktide's deferred
skinner asserts when one character's bone buffer is used twice in a frame
("Bad Skinner!", `bones_buffer.last_frame_accessed != frame`). Two guards
compare the buffer's last frame with the current one and branch past the
assert with a short `jne`; tools/stereo/set-skinner-assert-patch.ps1 turns each
into a `jmp` (docs/phase1/synchronized-stereo-probe.md).

This finds those guards in any build by structure rather than by offset: the
code that loads the address of the assertion's expression string, and the
short conditional jump just before it that skips over that load and the call.
It prints each site's RVA, file offset and bytes, and the executable's
SHA-256, which is what the patch tool's table of supported builds needs.
Read-only; needs `capstone` and `pefile`.

    python -B find-skinner-assert-sites.py <Darktide.exe>
"""
import hashlib
import sys

import capstone
import pefile

EXPRESSION = b"bones_buffer.last_frame_accessed != frame"
LOOKBACK = 0x60  # bytes before the string load to search for the guard


def main(path: str) -> int:
    data = open(path, "rb").read()
    pe = pefile.PE(data=data, fast_load=True)
    base = pe.OPTIONAL_HEADER.ImageBase
    text = next(s for s in pe.sections if s.Name.rstrip(b"\0") == b".text")
    code = text.get_data()
    text_rva = text.VirtualAddress

    # The start of every string containing the expression: the second guard's
    # is `last_bones_buffer.last_frame_accessed != frame`, which a load
    # addresses at its first character, not at the match inside it.
    strings = []
    start = 0
    while (found := data.find(EXPRESSION, start)) >= 0:
        begin = data.rfind(b"\0", 0, found) + 1
        rva = pe.get_rva_from_offset(begin)
        if rva is not None and rva not in strings:
            strings.append(rva)
        start = found + 1
    print(f"sha256={hashlib.sha256(data).hexdigest()}")
    print(f"expression_strings={','.join(hex(r) for r in strings) or 'none'}")
    if not strings:
        return 1

    # Every RIP-relative LEA of the string: 48/4C 8D 05/0D/15/1D/25/2D/35/3D disp32.
    loads = []
    for index in range(len(code) - 7):
        if code[index] in (0x48, 0x4C) and code[index + 1] == 0x8D and \
                (code[index + 2] & 0xC7) == 0x05:
            displacement = int.from_bytes(code[index + 3:index + 7], "little", signed=True)
            target = text_rva + index + 7 + displacement
            if target in strings:
                loads.append(text_rva + index)
    print(f"string_loads={','.join(hex(r) for r in loads) or 'none'}")

    disassembler = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    sites = []
    for load in loads:
        # A short jne whose target lies past the load: the branch that skips
        # the assert. Decoded backwards by trying each start offset and
        # keeping a decode that lands exactly on the load.
        best = None
        for back in range(2, LOOKBACK):
            offset = load - back - text_rva
            window = code[offset:load - text_rva]
            instructions = list(disassembler.disasm(window, text_rva + offset))
            if not instructions or instructions[-1].address + instructions[-1].size != load:
                continue
            for instruction in instructions:
                if instruction.mnemonic == "jne" and instruction.size == 2:
                    target = int(instruction.op_str, 16)
                    if target > load:
                        best = (instruction, instructions)
            if best:
                break
        if best is None:
            print(f"load {hex(load)}: no short jne skipping it")
            continue
        jump, context = best
        file_offset = pe.get_offset_from_rva(jump.address)
        raw = data[file_offset:file_offset + 2]
        sites.append((jump.address, file_offset, raw))
        compare = next((i for i in reversed(context) if i.address < jump.address and
                        i.mnemonic == "cmp"), None)
        print(f"site rva={hex(jump.address)} file_offset={hex(file_offset)} "
              f"bytes={raw.hex(' ')} jne_target={jump.op_str} "
              f"compare={compare.mnemonic + ' ' + compare.op_str if compare else 'none'}")
    print(f"sites={len(sites)}")
    return 0 if len(sites) == 2 and all(raw[0] == 0x75 for _, _, raw in sites) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
