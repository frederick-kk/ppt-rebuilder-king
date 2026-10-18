"""Finite explicit formulas to Office Math, with editable PPTX fallbacks.

This is deliberately not a general LaTeX engine or a formula classifier.  Only
callers that explicitly mark standalone equations should invoke this module.
Unsupported input raises ValueError and attachment keeps the original shape.
The implementation uses the Python standard library only.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import io
import os
from pathlib import Path
import re
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
from zipfile import BadZipFile, ZipFile


NS = {
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a14": "http://schemas.microsoft.com/office/drawing/2010/main",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
}
for _prefix, _namespace in NS.items():
    ET.register_namespace(_prefix, _namespace)


def _q(prefix: str, name: str) -> str:
    return "{" + NS[prefix] + "}" + name


_SYMBOLS = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "varepsilon": "ϵ", "zeta": "ζ", "eta": "η", "theta": "θ", "vartheta": "ϑ",
    "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ",
    "omicron": "ο", "pi": "π", "varpi": "ϖ", "rho": "ρ", "varrho": "ϱ",
    "sigma": "σ", "varsigma": "ς", "tau": "τ", "upsilon": "υ", "phi": "ϕ",
    "varphi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω", "Gamma": "Γ",
    "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ", "Pi": "Π",
    "Sigma": "Σ", "Upsilon": "Υ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
    "infty": "∞", "partial": "∂", "nabla": "∇", "times": "×", "cdot": "·",
    "pm": "±", "mp": "∓", "le": "≤", "leq": "≤", "ge": "≥", "geq": "≥",
    "ne": "≠", "neq": "≠", "approx": "≈", "equiv": "≡", "in": "∈",
    "notin": "∉", "subset": "⊂", "subseteq": "⊆", "supset": "⊃", "supseteq": "⊇",
    "cup": "∪", "cap": "∩", "emptyset": "∅", "forall": "∀", "exists": "∃",
    "to": "→", "rightarrow": "→", "leftarrow": "←", "leftrightarrow": "↔",
    "Rightarrow": "⇒", "Leftarrow": "⇐", "Leftrightarrow": "⇔", "prime": "′",
    "ldots": "…", "cdots": "⋯", "perp": "⊥", "parallel": "∥",
}
_FUNCTIONS = frozenset(("sin", "cos", "ln", "log", "exp", "sat"))
_LIMIT_FUNCTIONS = frozenset(("min", "max", "argmin", "argmax", "lim"))
_NARY = {"sum": "∑", "prod": "∏", "int": "∫"}
_NUMBER_SETS = dict(zip("RNZCQ", "ℝℕℤℂℚ"))
_OPERAND_BOUNDARIES = frozenset("+-−=<>≤≥≠≈≡∈∉⊂⊆⊃⊇∝∼≃≅≪≫∥⊥,;:→↔⇒⇐⇔±∓")
_CJK = re.compile("[\u2e80-\u9fff\uac00-\ud7af\uf900-\ufaff\U00020000-\U000323af]")


@dataclass(frozen=True)
class _Node:
    kind: str
    value: str = ""
    args: tuple[_Node, ...] = ()


def _seq(nodes=()) -> _Node:
    return _Node("sequence", args=tuple(nodes))


def _script(base: _Node, sub: _Node | None, sup: _Node | None) -> _Node:
    if sub is not None and sup is not None:
        return _Node("subsup", args=(base, sub, sup))
    if sub is not None:
        return _Node("sub", args=(base, sub))
    if sup is not None:
        return _Node("sup", args=(base, sup))
    return base


class _Parser:
    def __init__(self, text: str):
        self.text = text
        self.pos = 0
        self.depth = 0
        self.closes = []

    def _error(self, message: str):
        raise ValueError(f"{message} at character {self.pos + 1}")

    def _space(self):
        while self.pos < len(self.text) and self.text[self.pos] == " ":
            self.pos += 1

    def _starts(self, token: str) -> bool:
        return self.text.startswith(token, self.pos)

    def sequence(self, close: str | None = None) -> _Node:
        result = []
        self.closes.append(close)
        try:
            while self.pos < len(self.text):
                if close and self._starts(close):
                    self.pos += len(close)
                    return _seq(result)
                if self.text[self.pos] in "})]" or self._starts(r"\rVert"):
                    self._error("Mismatched delimiter")
                result.append(self.term())
            if close:
                self._error("Unclosed delimiter")
            return _seq(result)
        finally:
            self.closes.pop()

    def group(self) -> _Node:
        self._space()
        if not self._starts("{"):
            self._error("Expected a braced argument")
        self.pos += 1
        result = self.sequence("}")
        if not result.args or all(x.kind == "atom" and not x.value.strip() for x in result.args):
            self._error("Empty argument")
        return result

    def primary(self) -> _Node:
        if self.pos >= len(self.text):
            self._error("Missing expression")
        char = self.text[self.pos]
        if char in "_^'′":
            self._error("Script or prime has no base")
        if char == "{":
            return self.group()
        if char in "([":
            self.pos += 1
            close = ")" if char == "(" else "]"
            contents = self.sequence(close)
            if not contents.args:
                self._error("Empty delimiter")
            return _Node("delimiter", char + close, (contents,))
        for norm in ("||", "‖", r"\lVert"):
            if self._starts(norm):
                self.pos += len(norm)
                if norm == r"\lVert":
                    self._space()
                contents = self.sequence(r"\rVert" if norm == r"\lVert" else norm)
                if not contents.args:
                    self._error("Empty norm")
                return _Node("delimiter", "‖‖", (contents,))
        if char == "\\":
            self.pos += 1
            match = re.match(r"[A-Za-z]+", self.text[self.pos:])
            if not match:
                if self.pos < len(self.text) and self.text[self.pos] in ",;:! ":
                    spacing = self.text[self.pos]
                    self.pos += 1
                    return _Node("atom", "" if spacing == "!" else " ")
                self._error("Unsupported escape")
            command = match.group()
            self.pos += len(command)
            if command in _SYMBOLS:
                return _Node("atom", _SYMBOLS[command])
            if command in _NARY:
                return _Node("nary-name", _NARY[command])
            if command in _FUNCTIONS or command in _LIMIT_FUNCTIONS:
                return _Node("function-name", command)
            if command == "frac":
                return _Node("fraction", args=(self.group(), self.group()))
            if command == "sqrt":
                self._space()
                degree = _seq()
                if self._starts("["):
                    self.pos += 1
                    degree = self.sequence("]")
                    if not degree.args:
                        self._error("Empty root degree")
                return _Node("radical", args=(degree, self.group()))
            if command in ("mathbb", "operatorname"):
                content = self.group()
                if any(x.kind not in ("atom", "upright") for x in content.args):
                    self._error("Unsupported named symbol")
                name = "".join(x.value for x in content.args)
                if command == "mathbb" and name in _NUMBER_SETS:
                    return _Node("atom", _NUMBER_SETS[name])
                if command == "operatorname" and name in (_FUNCTIONS | _LIMIT_FUNCTIONS):
                    return _Node("function-name", name)
                self._error("Unsupported named symbol")
            if command == "underset":
                condition = self.group()
                base = self.group()
                if (len(base.args) == 1 and base.args[0].kind in ("function-name", "upright")
                        and base.args[0].value in (_FUNCTIONS | _LIMIT_FUNCTIONS)):
                    return _Node("limited-function-name", args=(base.args[0], condition))
                return _Node("lower", args=(base, condition))
            if command in ("quad", "qquad"):
                return _Node("atom", " " if command == "quad" else "  ")
            self._error(f"Unsupported command \\{command}")
        # Bare limit names are a small explicit convenience; arbitrary words are atoms.
        if char.isalpha() and (self.pos == 0 or not self.text[self.pos - 1].isalpha()):
            for name in sorted(_LIMIT_FUNCTIONS, key=len, reverse=True):
                end = self.pos + len(name)
                if self._starts(name) and (end == len(self.text) or not self.text[end].isalpha()):
                    self.pos = end
                    return _Node("function-name", name)
        if char in "})]|&#$%" or unicodedata.category(char) in ("Cc", "Cs", "Zl", "Zp"):
            self._error("Unsupported character or mismatched delimiter")
        self.pos += 1
        return _Node("atom", char)

    def script_argument(self) -> _Node:
        self._space()
        if self._starts("{"):
            return self.group()
        if self.pos >= len(self.text) or self.text[self.pos] in "_^})]":
            self._error("Missing script argument")
        # Common engineering notation P_G^max is accepted without adding general
        # unbraced multi-letter scripts (x_ij still means x_i followed by j).
        for name in ("max", "min"):
            end = self.pos + len(name)
            if self._starts(name) and (end == len(self.text) or not self.text[end].isalpha()):
                self.pos = end
                return _seq((_Node("upright", name),))
        return _seq((self.primary(),))

    def scripts(self) -> tuple[_Node | None, _Node | None]:
        sub = sup = None
        while True:
            saved = self.pos
            self._space()
            if self.pos >= len(self.text):
                self.pos = saved
                break
            char = self.text[self.pos]
            if char not in "_^'′":
                self.pos = saved
                break
            self.pos += 1
            if char == "_":
                if sub is not None:
                    self._error("Repeated subscript")
                sub = self.script_argument()
            elif char == "^":
                if sup is not None:
                    self._error("Repeated superscript")
                sup = self.script_argument()
            else:
                if sup is not None and not (sup.kind == "sequence" and all(x.kind == "atom" and set(x.value) <= {"′"} for x in sup.args)):
                    self._error("Prime and explicit superscript are ambiguous")
                sup = _seq((*(sup.args if sup else ()), _Node("atom", "′")))
        return sub, sup

    def operand(self) -> _Node:
        saved = self.pos
        self._space()
        result = []
        while self.pos < len(self.text):
            char = self.text[self.pos]
            if char in "})]" or self._starts(r"\rVert"):
                break
            if self.closes and self.closes[-1] and self._starts(self.closes[-1]):
                break
            if char in _OPERAND_BOUNDARIES:
                break
            # Symbol commands that denote a binary relation also end an operand.
            if char == "\\":
                match = re.match(r"\\([A-Za-z]+)", self.text[self.pos:])
                if match and _SYMBOLS.get(match.group(1)) in _OPERAND_BOUNDARIES:
                    break
            result.append(self.term())
        while result and result[-1].kind == "atom" and not result[-1].value.strip():
            result.pop()
        if not result:
            self.pos = saved
        return _seq(result)

    def term(self) -> _Node:
        self.depth += 1
        try:
            if self.depth > 96:
                self._error("Formula nesting exceeds the finite parser limit")
            base = self.primary()
            if base.kind == "atom" and not base.value.strip():
                return base
            sub, sup = self.scripts()
            if (sub is not None or sup is not None) and base.kind == "atom" and base.value in _OPERAND_BOUNDARIES:
                self._error("Script has no mathematical base")
            if base.kind == "nary-name":
                return _Node("nary", base.value, (sub or _seq(), sup or _seq(), self.operand()))
            if base.kind in ("function-name", "limited-function-name"):
                if base.kind == "limited-function-name":
                    name = _Node("lower", args=(_Node("upright", base.args[0].value), base.args[1]))
                else:
                    name = _Node("upright", base.value)
                if base.value in _LIMIT_FUNCTIONS:
                    if sub is not None:
                        name = _Node("lower", args=(name, sub))
                    if sup is not None:
                        name = _Node("upper", args=(name, sup))
                else:
                    name = _script(name, sub, sup)
                operand = self.operand()
                return _Node("function", args=(name, operand)) if operand.args else name
            return _script(base, sub, sup)
        finally:
            self.depth -= 1


def _element(name: str, parent=None, **attrs):
    element = ET.Element(_q("m", name), {_q("m", k): str(v) for k, v in attrs.items()})
    if parent is not None:
        parent.append(element)
    return element


def _argument(parent, name: str, node: _Node):
    target = _element(name, parent)
    _emit(node, target)
    return target


def _emit(node: _Node, parent):
    kind = node.kind
    if kind == "sequence":
        for child in node.args:
            _emit(child, parent)
    elif kind in ("atom", "upright", "function-name"):
        if not node.value:
            return
        run = _element("r", parent)
        if kind != "atom":
            _element("sty", _element("rPr", run), val="p")
        text = _element("t", run)
        if node.value != node.value.strip():
            text.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        text.text = node.value
    elif kind in ("sub", "sup", "subsup"):
        element = _element({"sub": "sSub", "sup": "sSup", "subsup": "sSubSup"}[kind], parent)
        _argument(element, "e", node.args[0])
        _argument(element, "sub" if kind != "sup" else "sup", node.args[1])
        if kind == "subsup":
            _argument(element, "sup", node.args[2])
    elif kind == "fraction":
        element = _element("f", parent)
        _argument(element, "num", node.args[0])
        _argument(element, "den", node.args[1])
    elif kind == "radical":
        element = _element("rad", parent)
        _element("degHide", _element("radPr", element), val="0" if node.args[0].args else "1")
        _argument(element, "deg", node.args[0])
        _argument(element, "e", node.args[1])
    elif kind == "delimiter":
        element = _element("d", parent)
        properties = _element("dPr", element)
        _element("begChr", properties, val=node.value[0])
        _element("endChr", properties, val=node.value[1])
        _element("grow", properties, val="1")
        _argument(element, "e", node.args[0])
    elif kind in ("lower", "upper"):
        element = _element("limLow" if kind == "lower" else "limUpp", parent)
        _argument(element, "e", node.args[0])
        _argument(element, "lim", node.args[1])
    elif kind == "function":
        element = _element("func", parent)
        _argument(element, "fName", node.args[0])
        _argument(element, "e", node.args[1])
    elif kind == "nary":
        sub, sup, operand = node.args
        if not operand.args:
            # A displayed operator with limits is complete even without an
            # operand. Never generate a phantom empty n-ary operand box.
            complete = _Node("upright", node.value)
            if sub.args:
                complete = _Node("lower", args=(complete, sub))
            if sup.args:
                complete = _Node("upper", args=(complete, sup))
            _emit(complete, parent)
            return
        element = _element("nary", parent)
        properties = _element("naryPr", element)
        _element("chr", properties, val=node.value)
        _element("limLoc", properties, val="subSup" if node.value == "∫" else "undOvr")
        _element("grow", properties, val="1")
        _element("subHide", properties, val="0" if sub.args else "1")
        _element("supHide", properties, val="0" if sup.args else "1")
        _argument(element, "sub", sub)
        _argument(element, "sup", sup)
        _argument(element, "e", operand)
    else:
        raise ValueError("Unsupported internal mathematical construct")


def formula_xml(text: str) -> str:
    """Return one namespaced m:oMath element, or raise ValueError.

    Finite syntax: Unicode atoms, balanced parentheses/brackets/braces,
    sub/superscripts and primes, frac, sqrt, sum/prod/int, min/max/argmin/
    argmax/lim, underset, sin/cos/ln/log/exp, operatorname{sat}, norms, common
    Greek/symbol commands, and mathbb{R,N,Z,C,Q}.  Matrices, cases, alignment,
    multiline formulas, arbitrary commands and CJK expressions are rejected.
    A function or n-ary operand consumes the following multiplicative sequence
    up to an additive/relational separator; braces make the extent explicit.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Formula must be a nonempty string")
    if len(text) > 16384:
        raise ValueError("Formula exceeds the finite parser length limit")
    if _CJK.search(text):
        raise ValueError("CJK expressions remain literal editable text")
    if any(unicodedata.category(char) in ("Cc", "Cs", "Zl", "Zp") for char in text):
        raise ValueError("Multiline or control characters are unsupported")
    parser = _Parser(text)
    try:
        node = parser.sequence()
    except RecursionError as exc:
        raise ValueError("Formula nesting exceeds the finite parser limit") from exc
    root = _element("oMath")
    _emit(node, root)
    if not len(root):
        raise ValueError("Formula contains no mathematical content")
    return ET.tostring(root, encoding="unicode")


def _shape_name(shape) -> str | None:
    properties = shape.find("p:nvSpPr/p:cNvPr", NS)
    return properties.get("name") if properties is not None else None


def _targets(root):
    """Yield one logical shape per normal shape or compatible fallback."""
    def walk(parent):
        for child in parent:
            if child.tag == _q("mc", "AlternateContent"):
                fallback = child.find("mc:Fallback/p:sp", NS)
                if fallback is not None:
                    yield parent, child, fallback
            elif child.tag == _q("p", "sp"):
                yield parent, child, child
            else:
                yield from walk(child)

    yield from walk(root)


def _cambria(properties):
    order = {name: index for index, name in enumerate(
        ("latin", "ea", "cs", "sym", "hlinkClick", "hlinkMouseOver", "rtl", "extLst"))}
    for name in ("latin", "ea", "cs"):
        font = properties.find("a:" + name, NS)
        if font is None:
            font = ET.Element(_q("a", name))
            index = next((index for index, child in enumerate(properties)
                          if order.get(child.tag.rsplit("}", 1)[-1], -1) > order[name]), len(properties))
            properties.insert(index, font)
        font.set("typeface", "Cambria Math")


def _native_shape(original, formula: str):
    native = deepcopy(original)
    textbody = native.find("p:txBody", NS)
    if textbody is None:
        raise ValueError("target has no editable text body")
    paragraphs = textbody.findall("a:p", NS)
    if not paragraphs:
        raise ValueError("target has no editable text paragraph")
    first = paragraphs[0]
    old_properties = first.find("a:pPr", NS)
    properties = deepcopy(old_properties) if old_properties is not None else ET.Element(_q("a", "pPr"))
    defaults = properties.find("a:defRPr", NS)
    if defaults is None:
        run_properties = first.find("a:r/a:rPr", NS)
        defaults = deepcopy(run_properties) if run_properties is not None else ET.Element(_q("a", "defRPr"))
        defaults.tag = _q("a", "defRPr")
        extension = properties.find("a:extLst", NS)
        index = list(properties).index(extension) if extension is not None else len(properties)
        properties.insert(index, defaults)
    _cambria(defaults)
    # One text paragraph is replaced by one math zone; the original paragraph
    # structure remains entirely unchanged in the compatibility fallback.
    for paragraph in paragraphs:
        textbody.remove(paragraph)
    paragraph = ET.SubElement(textbody, _q("a", "p"))
    paragraph.append(properties)
    math = ET.SubElement(paragraph, _q("a14", "m"))
    # MS-ODRAWXML 2.3.3.41 CT_TextMath requires m:oMath directly, rather than
    # the m:oMathPara intermediary found in some older prose/examples.
    math.append(ET.fromstring(formula))
    ending = deepcopy(defaults)
    ending.tag = _q("a", "endParaRPr")
    paragraph.append(ending)
    return native


def _serialize_slide(root, namespaces) -> bytes:
    # ElementTree drops declarations used only by mc:Ignorable / Requires
    # attribute values. Preserve such namespace bindings explicitly.
    for prefix, uri in namespaces:
        if prefix and prefix not in NS and not re.fullmatch(r"ns\d+", prefix):
            ET.register_namespace(prefix, uri)
    for prefix, uri in NS.items():
        ET.register_namespace(prefix, uri)
    raw = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    end = raw.find(b">", raw.find(b"?>") + 2)
    opening = raw[:end]
    additions = []
    from xml.sax.saxutils import quoteattr
    for prefix, uri in namespaces:
        declaration = ("xmlns:" + prefix if prefix else "xmlns").encode("utf-8")
        if declaration + b"=" not in opening and prefix not in ("xml",):
            additions.append(b" " + declaration + b"=" + quoteattr(uri).encode("utf-8"))
    return raw[:end] + b"".join(additions) + raw[end:]


def attach_equations(pptx: Path, equations: dict[str, str]) -> dict:
    """Replace explicitly named text shapes with compatible native equations.

    Returns embedded/fallback id lists and warnings. Missing or ambiguous ids
    never create shapes. Unsupported formulas retain the original literal text.
    The original p:sp becomes mc:Fallback; only the Choice contains native math.
    The package is atomically replaced only after a successful ZIP validation.
    This validates structure, not PowerPoint rendering or geometry fitting.
    """
    result = {"embedded": [], "fallback": [], "warnings": []}
    if not equations:
        return result
    pptx = Path(pptx)
    with ZipFile(pptx, "r") as archive:
        if archive.testzip() is not None:
            raise BadZipFile("PPTX contains a corrupt ZIP member")
        infos = archive.infolist()
        if len({info.filename for info in infos}) != len(infos):
            raise BadZipFile("PPTX has duplicate ZIP member names")
        members = {info.filename: archive.read(info) for info in infos}
        comment = archive.comment
    slides = {}
    targets = {}
    for name, data in members.items():
        if re.fullmatch(r"ppt/slides/slide\d+\.xml", name):
            try:
                namespaces = list(dict.fromkeys(pair for event, pair in ET.iterparse(io.BytesIO(data), events=("start-ns",))))
                root = ET.fromstring(data)
            except ET.ParseError as exc:
                raise ValueError(f"Invalid slide XML: {name}") from exc
            slides[name] = (root, namespaces)
            for parent, container, shape in _targets(root):
                shape_name = _shape_name(shape)
                if shape_name in equations:
                    targets.setdefault(shape_name, []).append((name, parent, container, shape))
    changed = set()
    for identity, expression in equations.items():
        matches = targets.get(identity, [])
        if len(matches) != 1:
            result["fallback"].append(identity)
            reason = "missing target text shape" if not matches else "ambiguous target text shape name"
            result["warnings"].append(f"{identity}: {reason}; no native equation was added")
            continue
        part, parent, container, original = matches[0]
        try:
            formula = formula_xml(expression)
            native = _native_shape(original, formula)
        except ValueError as exc:
            result["fallback"].append(identity)
            result["warnings"].append(f"{identity}: {exc}; original editable text retained")
            if container is not original:
                index = list(parent).index(container)
                parent.remove(container)
                parent.insert(index, original)
                changed.add(part)
            continue
        result["embedded"].append(identity)
        if container is not original:
            existing = container.find("mc:Choice[@Requires='a14']/p:sp", NS)
            if existing is not None and ET.tostring(existing) == ET.tostring(native):
                continue
        wrapper = ET.Element(_q("mc", "AlternateContent"))
        choice = ET.SubElement(wrapper, _q("mc", "Choice"), Requires="a14")
        choice.append(native)
        fallback = ET.SubElement(wrapper, _q("mc", "Fallback"))
        fallback.append(original)
        index = list(parent).index(container)
        parent.remove(container)
        parent.insert(index, wrapper)
        changed.add(part)
    if not changed:
        return result
    for part in changed:
        root, namespaces = slides[part]
        members[part] = _serialize_slide(root, namespaces)
        ET.fromstring(members[part])
    descriptor, temporary = tempfile.mkstemp(prefix=".newpath-math-", suffix=".pptx", dir=pptx.parent)
    os.close(descriptor)
    try:
        with ZipFile(temporary, "w") as archive:
            archive.comment = comment
            for info in infos:
                archive.writestr(info, members[info.filename])
        with ZipFile(temporary) as archive:
            if archive.testzip() is not None:
                raise BadZipFile("Equation output ZIP validation failed")
        os.replace(temporary, pptx)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return result
