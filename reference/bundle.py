"""Bundle the reference into a single-file ``emulator.py`` (candidate format).

Used by grader mutants (each applies source patches to chip8.py) and by
pipeline tests that need a known-good candidate. The bundled file never enters
an agent sandbox.
"""

from pathlib import Path

REFERENCE_DIR = Path(__file__).resolve().parent


class PatchError(Exception):
    pass


def apply_patches(source, patches):
    """Apply ``(old, new)`` or ``(old, new, count)`` text patches, checking counts."""
    for patch in patches:
        old, new = patch[0], patch[1]
        expected = patch[2] if len(patch) > 2 else 1
        found = source.count(old)
        if found != expected:
            raise PatchError(f"patch target found {found} times (expected {expected}): {old!r}")
        source = source.replace(old, new)
    return source


def bundle_source(variant="canonical", patches=()):
    core = (REFERENCE_DIR / "chip8.py").read_text()
    core = apply_patches(core, patches)
    cli_lines = []
    for line in (REFERENCE_DIR / "cli.py").read_text().splitlines():
        if "# bundle:strip" in line:
            continue
        if "# bundle:variant" in line:
            line = f"DEFAULT_VARIANT = {variant!r}"
        cli_lines.append(line)
    cli = "\n".join(cli_lines)
    return core.rstrip() + "\n\n\n# ---- CLI ----\n\n" + cli + "\n"


def write_bundle(path, variant="canonical", patches=()):
    path = Path(path)
    path.write_text(bundle_source(variant, patches))
    return path
