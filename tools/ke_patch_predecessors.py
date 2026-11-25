"""Recognize predecessor patches whose context was changed by visibility installers."""
from pathlib import Path

from tools.ke_dagor_visibility import SCREEN_SNAPSHOT


def predecessor_additions_present(dagor: Path, patch: Path) -> bool:
    """Require every contiguous added block, not just a feature marker.

    Only called for explicitly known predecessors of the visibility/composition
    changes. The screenshot snapshot is the one known insertion inside an
    older added block; remove it from the in-memory comparison only.
    No files are modified.
    """
    name = None
    source = None
    blocks = []
    pending = []

    def flush():
        if pending:
            blocks.append((source, "".join(pending)))
            pending.clear()

    for line in patch.read_text(encoding="utf-8").splitlines(keepends=True):
        if line.startswith("+++ b/"):
            flush()
            name = line[6:].strip()
            target = dagor / name
            if not target.is_file():
                return False
            source = target.read_text(encoding="utf-8")
            if name.endswith("/kolobanovEye.cpp"):
                from tools.ke_dagor_fixed_camera import normalize_fixed_capture
                source = normalize_fixed_capture(source)
            if name.endswith("/dynRender.cpp"):
                if source.count(SCREEN_SNAPSHOT) == 1:
                    source = source.replace(SCREEN_SNAPSHOT, "", 1)
        elif line.startswith("+") and not line.startswith("+++"):
            if source is None:
                return False
            pending.append(line[1:])
        else:
            flush()
    flush()
    return bool(blocks) and all(block in content for content, block in blocks)
