"""Tier-A ASW parser fallback: a pycparser-backed reader for ERT-style headers
that don't fit the tight regex contract (multi-line declarations, unusual
spacing, typedef'd wrappers). Opt in per model with `parser: c`.

Needs the ``[parse]`` extra (pycparser). The rtw headers aren't preprocessed C,
so we strip #-directives + comments and prepend `typedef` stubs for the rtw
types (the AST keeps the original type *name*, e.g. `uint16_T`), then walk the
C AST for extern data (signals / params) and `void(void)` entry points —
producing the same ModelInterface as parse/ert.py.
"""
import re
from pathlib import Path

from .ert import (_EXTERN_FUNC, RTW_TYPES, Calibration, ModelInterface, Signal,
                  _direction)


def available():
    try:
        import pycparser  # noqa: F401
        return True
    except ImportError:
        return False


def _preprocess(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)   # block comments
    text = re.sub(r"//[^\n]*", "", text)                # line comments
    text = re.sub(r"^[ \t]*#.*$", "", text, flags=re.M)  # directives/guards
    return text


def _stub_types():
    # make every rtw type a valid typedef so pycparser accepts the decls; the
    # AST still reports the original type name.
    return "".join(f"typedef int {t};\n" for t in RTW_TYPES)


def _walk(text, parser):
    """Return (data_decls, funcs) from one header: data_decls = [(name, ctype,
    dim)], funcs = [name] for extern void <f>(void)."""
    from pycparser import c_ast
    ast = parser.parse(_stub_types() + _preprocess(text))
    data, funcs = [], []
    stub = set(RTW_TYPES)
    for ext in ast.ext:
        if not isinstance(ext, c_ast.Decl):
            continue
        t = ext.type
        if isinstance(t, c_ast.FuncDecl):
            funcs.append(ext.name)
            continue
        dim = 1
        if isinstance(t, c_ast.ArrayDecl):
            if isinstance(t.dim, c_ast.Constant):
                dim = int(t.dim.value)
            t = t.type
        if isinstance(t, c_ast.TypeDecl) and isinstance(
                t.type, c_ast.IdentifierType):
            ctype = " ".join(t.type.names)
            if ext.name not in stub:            # skip the injected typedefs
                data.append((ext.name, ctype, dim))
    return data, funcs


def parse_model_c(codegen_dir, model_name):
    from pycparser import CParser
    d = Path(codegen_dir)

    def read(suffix):
        p = d / f"{model_name}{suffix}"
        if not p.exists():
            raise FileNotFoundError(
                f"erosgen: model '{model_name}': expected {p} (parser: c)")
        return p.read_text()

    parser = CParser()
    # pycparser handles the narrow, struct-free port/param surface (more robust
    # than the regex for multi-line / qualified declarations)...
    sig_data, _ = _walk(read("_Intfc.h"), parser)
    cal_data, _ = _walk(read("_Param.h"), parser)
    # ...but the full <model>.h carries rtw structs/typedefs that need the whole
    # rtwtypes include tree, so the void(void) entry points stay a robust regex.
    funcs = [m.group(1) for m in _EXTERN_FUNC.finditer(read(".h"))]

    signals = tuple(Signal(n, ct, _direction(n), dim)
                    for n, ct, dim in sig_data)
    calibrations = tuple(Calibration(n, ct, "extern") for n, ct, _ in cal_data)
    init_fn = next((f for f in funcs if f == f"{model_name}_initialize"), "")
    runnables = tuple(f for f in funcs if f != init_fn)
    return ModelInterface(model_name, init_fn, runnables, signals, calibrations)
