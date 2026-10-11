"""CLI contract (SPEC.md Section 5.7) for the reference emulator.

    python -m reference.cli --rom PATH --cycles N [--keys KEYS.json] [--seed S]
                            [--frames-at C1,C2,...] [--variant canonical|variant_v]

``--variant`` is a reference-only extension; candidate emulators implement a
single machine and do not take it.
"""

import argparse
import json
import sys

try:  # bundle:strip
    from reference.chip8 import CONFIGS, run  # bundle:strip
except ImportError:  # bundle:strip
    from chip8 import CONFIGS, run  # bundle:strip

DEFAULT_VARIANT = "canonical"  # bundle:variant


def parse_frames_at(text):
    if not text:
        return []
    return [int(part) for part in text.split(",") if part.strip()]


def load_keys(path):
    if not path:
        return []
    with open(path) as f:
        events = json.load(f)
    for event in events:
        if not (isinstance(event.get("key"), int) and 0 <= event["key"] <= 0xF):
            raise ValueError(f"bad key event: {event!r}")
    return events


def main(argv=None):
    parser = argparse.ArgumentParser(description="CHIP-8 reference emulator")
    parser.add_argument("--rom", required=True)
    parser.add_argument("--cycles", required=True, type=int)
    parser.add_argument("--keys")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--frames-at", default="")
    parser.add_argument("--variant", default=DEFAULT_VARIANT, choices=sorted(CONFIGS))
    args = parser.parse_args(argv)

    with open(args.rom, "rb") as f:
        rom = f.read()
    result = run(
        rom,
        args.cycles,
        keys=load_keys(args.keys),
        seed=args.seed,
        frames_at=parse_frames_at(args.frames_at),
        config=CONFIGS[args.variant],
    )
    sys.stdout.write(json.dumps(result, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
