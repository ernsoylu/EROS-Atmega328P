"""Protected-region (USER CODE) merge for the once-only skeletons.

The skeleton emitters (``emit/skeletons.py`` main.c + asw_<rate>ms.c,
``emit/asw.py`` the hand-ASW ``<name>.c`` body) wrap every developer-editable
span in paired markers::

    /* USER CODE BEGIN <id> */ ... /* USER CODE END <id> */

with a stable ``<id>`` derived from the YAML element (e.g. ``TASK_BUTTON_BODY``).
Regeneration then *merges* rather than freezes: the scaffold (banner, includes,
signatures, ``TerminateTask``) is refreshed from the YAML, while the bytes
inside each marker pair are carried over from the on-disk file, matched by
``<id>`` (not line number) — so a reorder never strands user code, and a rename
surfaces as an ``ORPHAN_USER_BLOCK`` warning instead of a silent loss.

``cli.write`` calls :func:`merge` only for the once-files (``overwrite=False``)
and only when the on-disk file already carries markers; a legacy marker-less
file is still kept untouched, so pre-existing projects never get clobbered.
"""
import re

# Leading whitespace is captured/allowed so a body marker can be indented; the
# id is a single token. Trailing whitespace tolerated for hand-edited files.
_BEGIN = re.compile(r"^\s*/\* USER CODE BEGIN (\S+) \*/\s*$")
_END = re.compile(r"^\s*/\* USER CODE END (\S+) \*/\s*$")


def begin(rid):
    return f"/* USER CODE BEGIN {rid} */"


def end(rid):
    return f"/* USER CODE END {rid} */"


def has_markers(text):
    return "USER CODE BEGIN" in text


class MergeError(Exception):
    """Malformed markers on disk — the caller keeps the file rather than risk
    losing data."""


def extract_regions(text):
    """Map region id -> inner text (lines strictly between BEGIN/END, joined
    with '\\n'; '' for an empty region). Raise MergeError on an unbalanced,
    mismatched, or duplicated marker."""
    regions = {}
    open_id = None
    buf = []
    for n, line in enumerate(text.splitlines(), 1):
        mb = _BEGIN.match(line)
        me = _END.match(line)
        if mb:
            if open_id is not None:
                raise MergeError(f"line {n}: USER CODE BEGIN {mb.group(1)} "
                                 f"inside still-open region {open_id}")
            open_id, buf = mb.group(1), []
        elif me:
            if open_id is None:
                raise MergeError(f"line {n}: USER CODE END {me.group(1)} "
                                 "without a matching BEGIN")
            if me.group(1) != open_id:
                raise MergeError(f"line {n}: USER CODE END {me.group(1)} does "
                                 f"not match open BEGIN {open_id}")
            if open_id in regions:
                raise MergeError(f"duplicate USER CODE region '{open_id}'")
            regions[open_id] = "\n".join(buf)
            open_id = None
        elif open_id is not None:
            buf.append(line)
    if open_id is not None:
        raise MergeError(f"unterminated USER CODE region '{open_id}'")
    return regions


def _graveyard(saved, orphans):
    """A compile-safe #if 0 block preserving orphaned user regions verbatim, in
    a deterministic (sorted) order so re-merge is a fixed point."""
    out = ["",
           "#if 0  /* ORPHANED USER CODE: no matching app.yaml element.",
           "          Move each block into a live USER CODE region, then delete",
           "          this #if 0 ... #endif. erosgen preserves it meanwhile. */"]
    for rid in sorted(orphans):
        out.append(begin(rid))
        if saved[rid] != "":
            out.extend(saved[rid].split("\n"))
        out.append(end(rid))
    out.append("#endif")
    return out


def merge(fresh, existing, sink=None, where=""):
    """Re-inject the user regions captured from ``existing`` into ``fresh`` (a
    freshly emitted skeleton whose regions hold only seed text). Regions present
    on disk but absent from ``fresh`` are reported ``ORPHAN_USER_BLOCK`` and
    preserved in an #if 0 graveyard so no code is ever lost. Returns the merged
    text; on malformed on-disk markers returns ``existing`` unchanged."""
    try:
        saved = extract_regions(existing)
    except MergeError as e:
        if sink is not None:
            sink.warning("MERGE_PARSE",
                         f"{where}: cannot parse USER CODE markers ({e}); left "
                         "the file unchanged", where)
        return existing

    out = []
    consumed = set()
    lines = fresh.splitlines()
    i = 0
    while i < len(lines):
        mb = _BEGIN.match(lines[i])
        if not mb:
            out.append(lines[i])
            i += 1
            continue
        rid = mb.group(1)
        out.append(lines[i])                      # BEGIN from the fresh scaffold
        j = i + 1
        while j < len(lines) and not _END.match(lines[j]):
            j += 1
        if rid in saved:                          # user bytes win over the seed
            consumed.add(rid)
            if saved[rid] != "":
                out.extend(saved[rid].split("\n"))
        else:                                     # brand-new region: keep seed
            out.extend(lines[i + 1:j])
        out.append(lines[j])                      # END from the fresh scaffold
        i = j + 1

    orphans = [rid for rid in saved if rid not in consumed]
    if orphans:
        for rid in sorted(orphans):
            if sink is not None:
                sink.warning("ORPHAN_USER_BLOCK",
                             f"{where}: USER CODE region '{rid}' has no matching "
                             "app.yaml element; preserved in an #if 0 block at "
                             "end of file — relocate it, then delete that block.",
                             where)
        out += _graveyard(saved, orphans)

    text = "\n".join(out)
    return text if text.endswith("\n") else text + "\n"
