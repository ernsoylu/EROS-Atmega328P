"""Phase 13 - erosproject.yaml: a workspace of app.yamls with variant overlays.

Today one app.yaml == one application. A workspace aggregates several under one
file so a product line shares one generate command and a build posture::

  name: blink_product
  variants:
    debug:   { system: { hooks: { error: true, startup: true } } }
    release: { system: { budget: { flash: 3072, ram: 512 } } }
  apps:
    - nano/app.yaml
    - uno/app.yaml

``erosgen erosproject.yaml [--variant NAME]`` generates every listed app. When a
variant is selected its map is deep-merged over each app's doc (variant wins), so
debug/release differ by overlay, not by duplicated app.yamls.
"""
from pathlib import Path

import yaml


def is_workspace(doc):
    """A doc is a workspace iff it has an ``apps:`` list (vs a single app.yaml)."""
    return isinstance(doc, dict) and isinstance(doc.get("apps"), list)


def deep_merge(base, over):
    """Recursively merge ``over`` onto ``base``: dicts merge key-wise; scalars and
    lists in ``over`` replace ``base`` wholesale (a list is an atomic value)."""
    if isinstance(base, dict) and isinstance(over, dict):
        out = dict(base)
        for k, v in over.items():
            out[k] = deep_merge(out[k], v) if k in out else v
        return out
    return over


def load_workspace(path, variant=None):
    """Return ``(name, [(app_path, merged_doc), ...])`` for the workspace at
    ``path``. Each app's doc has the selected variant's overlay deep-merged over
    it. Raises ValueError on an unknown variant, FileNotFoundError on a missing
    app.yaml."""
    p = Path(path)
    doc = yaml.safe_load(p.read_text()) or {}
    variants = doc.get("variants") or {}
    if variant is not None and variant not in variants:
        raise ValueError(
            f"{p.name}: unknown variant '{variant}' (have {sorted(variants)})")
    overlay = variants.get(variant, {}) if variant else {}

    apps = []
    for entry in (doc.get("apps") or []):
        rel = entry if isinstance(entry, str) else (entry or {}).get("path")
        if not rel:
            continue
        app_path = (p.parent / rel).resolve()
        if not app_path.exists():
            raise FileNotFoundError(
                f"{p.name}: app '{rel}' -> {app_path} not found")
        app_doc = yaml.safe_load(app_path.read_text()) or {}
        apps.append((app_path, deep_merge(app_doc, overlay)))
    return doc.get("name", p.stem), apps
