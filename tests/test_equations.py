"""Independent finite-math and synthetic package acceptance tests."""
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

try:
    from newpath import equations
except ImportError:
    equations = None

NS = {
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a14": "http://schemas.microsoft.com/office/drawing/2010/main",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
}


def text_of(node):
    return "".join(t.text or "" for t in node.iter("{" + NS["m"] + "}t"))


class FormulaTests(unittest.TestCase):
    def math(self, text):
        self.assertIsNotNone(equations, "finite-math implementation is missing")
        result = equations.formula_xml(text)
        self.assertIn("xmlns:m=", result)
        root = ET.fromstring(result)
        self.assertEqual(root.tag, "{" + NS["m"] + "}oMath")
        return root

    def test_plain_atoms_and_unicode_remain_office_math_runs(self):
        root = self.math("α+β=μ·θ ≤ ∞")
        self.assertEqual(text_of(root), "α+β=μ·θ ≤ ∞")
        self.assertGreater(len(root.findall("m:r", NS)), 0)

    def test_structured_subscript_and_superscript_contents(self):
        for source, expected in [
            ("x_i^{k+1}", ("x", "i", "k+1")),
            ("x_i^*", ("x", "i", "*")),
            ("μ_i^{*T}", ("μ", "i", "*T")),
            ("P_G^max", ("P", "G", "max")),
            ("x^2_i", ("x", "i", "2")),
        ]:
            with self.subTest(source=source):
                root = self.math(source)
                node = root.find("m:sSubSup", NS)
                self.assertIsNotNone(node)
                self.assertEqual(tuple(text_of(node.find("m:" + key, NS))
                                       for key in ("e", "sub", "sup")), expected)

    def test_single_scripts_and_derivative_primes(self):
        for source, tag, expected in [
            ("x_i", "sSub", "i"), ("x^{2n}", "sSup", "2n"),
            ("f'", "sSup", "′"), ("f′′", "sSup", "′′"),
            ("f_i'", "sSubSup", "′"),
        ]:
            with self.subTest(source=source):
                root = self.math(source)
                node = root.find("m:" + tag, NS)
                self.assertIsNotNone(node)
                arg = "sub" if tag == "sSub" else "sup"
                self.assertEqual(text_of(node.find("m:" + arg, NS)), expected)

    def test_fraction_keeps_structured_numerator_and_denominator(self):
        root = self.math(r"\frac{x_i+1}{\sqrt{a^2+b^2}}")
        frac = root.find("m:f", NS)
        self.assertIsNotNone(frac.find("m:num/m:sSub", NS))
        self.assertIsNotNone(frac.find("m:den/m:rad", NS))
        self.assertEqual(text_of(frac.find("m:num", NS)), "xi+1")

    def test_root_degree_and_hide_are_explicit(self):
        square = self.math(r"\sqrt{x}").find("m:rad", NS)
        self.assertEqual(square.find("m:radPr/m:degHide", NS).get("{" + NS["m"] + "}val"), "1")
        self.assertEqual(text_of(square.find("m:e", NS)), "x")
        cube = self.math(r"\sqrt[n+1]{x}").find("m:rad", NS)
        self.assertEqual(text_of(cube.find("m:deg", NS)), "n+1")

    def test_nary_operators_have_real_limits_and_operand(self):
        for command, symbol in [("sum", "∑"), ("prod", "∏"), ("int", "∫")]:
            with self.subTest(command=command):
                root = self.math("\\" + command + "_{i=1}^{N} x_i+1")
                node = root.find("m:nary", NS)
                self.assertIsNotNone(node)
                self.assertEqual(node.find("m:naryPr/m:chr", NS).get("{" + NS["m"] + "}val"), symbol)
                self.assertEqual(text_of(node.find("m:sub", NS)), "i=1")
                self.assertEqual(text_of(node.find("m:sup", NS)), "N")
                self.assertEqual(text_of(node.find("m:e", NS)), "xi")
                self.assertIn("+1", text_of(root))

    def test_membership_relation_is_outside_nary_operand(self):
        root = self.math(r"\sum_i x_i\in R")
        node = root.find("m:nary", NS)
        self.assertEqual(text_of(node.find("m:e", NS)), "xi")
        self.assertIn("∈", text_of(root))

    def test_nary_can_consume_braced_or_parenthesized_sequence(self):
        for source in [r"\sum_{i=1}^{N}{x_i+y_i}", r"\sum_{i=1}^{N}(x_i+y_i)"]:
            with self.subTest(source=source):
                node = self.math(source).find("m:nary", NS)
                self.assertIsNotNone(node.find("m:e/m:sSub", NS) or node.find("m:e/m:d", NS))
                self.assertIn("+", text_of(node.find("m:e", NS)))

    def test_bare_nary_limit_operator_has_no_empty_operand_block(self):
        root = self.math(r"\sum_{i=1}^{N}")
        self.assertIsNone(root.find(".//m:nary", NS))
        self.assertIsNotNone(root.find(".//m:limLow", NS))
        self.assertIsNotNone(root.find(".//m:limUpp", NS))
        self.assertIn("∑", text_of(root))
        self.assertEqual(len(root.findall(".//m:e", NS)), 2)
        self.assertTrue(all(len(x) for x in root.findall(".//m:e", NS)))

    def test_limit_functions_store_conditions_below_function_name(self):
        for name in ["min", "max", "argmin", "argmax", "lim"]:
            with self.subTest(name=name):
                root = self.math("\\" + name + "_{x∈R} f(x)")
                func = root.find("m:func", NS)
                self.assertIsNotNone(func)
                low = func.find("m:fName/m:limLow", NS)
                self.assertEqual(text_of(low.find("m:e", NS)), name)
                self.assertEqual(text_of(low.find("m:lim", NS)), "x∈R")
                self.assertGreater(len(func.find("m:e", NS)), 0)

    def test_standard_functions_and_operatorname_are_function_objects(self):
        for source, name in [(r"\sin(x)", "sin"), (r"\cos x", "cos"),
                             (r"\ln x", "ln"), (r"\log_2 x", "log"),
                             (r"\exp{x+1}", "exp"), (r"\operatorname{sat}(x)", "sat")]:
            with self.subTest(source=source):
                root = self.math(source)
                func = root.find("m:func", NS)
                self.assertIsNotNone(func)
                self.assertIn(name, text_of(func.find("m:fName", NS)))
                self.assertGreater(len(func.find("m:e", NS)), 0)

    def test_norms_are_delimiters_with_scripts_on_entire_norm(self):
        for source in [r"||x||_2^2", "‖x‖_2^2", r"\lVert x\rVert_2^2"]:
            with self.subTest(source=source):
                scripted = self.math(source).find("m:sSubSup", NS)
                delimiter = scripted.find("m:e/m:d", NS)
                self.assertEqual(delimiter.find("m:dPr/m:begChr", NS).get("{" + NS["m"] + "}val"), "‖")
                self.assertEqual(text_of(delimiter.find("m:e", NS)), "x")
                self.assertEqual(text_of(scripted.find("m:sub", NS)), "2")

    def test_mathbb_maps_finite_number_sets(self):
        root = self.math(r"x∈\mathbb{R}^n, z∈\mathbb{C}, k∈\mathbb{N}, a∈\mathbb{Z}, q∈\mathbb{Q}")
        for character in "ℝℂℕℤℚ":
            self.assertIn(character, text_of(root))

    def test_explicit_underset_and_greek_commands(self):
        root = self.math(r"\underset{x\to0}{\lim}\frac{\sin x}{x}=\alpha+\mu")
        self.assertIsNotNone(root.find(".//m:limLow", NS))
        self.assertIsNotNone(root.find(".//m:f", NS))
        self.assertIn("x→0", text_of(root))
        self.assertIn("α+μ", text_of(root))

    def test_underset_limit_function_keeps_its_operand(self):
        root = self.math(r"\underset{x\in\mathbb{R}}{\argmin} f(x)")
        function = root.find("m:func", NS)
        self.assertIsNotNone(function)
        self.assertIsNotNone(function.find("m:fName/m:limLow", NS))
        self.assertIn("f", text_of(function.find("m:e", NS)))

    def test_nary_and_function_operands_can_begin_with_norms(self):
        for source, tag in [(r"\sum_i ||x_i||^2", "nary"), (r"\exp ‖x‖", "func")]:
            with self.subTest(source=source):
                node = self.math(source).find("m:" + tag, NS)
                self.assertIsNotNone(node)
                self.assertIsNotNone(node.find("m:e//m:d", NS))

    def test_all_delimiter_variants_reject_empty_contents(self):
        for source in ["{}", "()", "[]", "||||", "‖‖", r"\lVert\rVert", r"\sqrt[]{x}"]:
            with self.subTest(source=source):
                with self.assertRaises(ValueError):
                    equations.formula_xml(source)

    def test_malformed_or_unsupported_is_rejected_instead_of_flattened(self):
        self.assertIsNotNone(equations, "finite-math implementation is missing")
        for source in ["", " ", "x_{i", "x_i}", "{x", "x^", "_i", "x^^2", "x_i_j",
                       r"\unknown{x}", r"\frac{x}", r"\frac{}{x}", r"\sqrt{}", "(x]", "||x",
                       r"\begin{matrix}a&b\end{matrix}", r"\cases{x}", "x\ny", r"x\\y", "中文+x",
                       r"\mathbb{A}", r"\operatorname{unsupported}(x)", r"\sum_{} x"]:
            with self.subTest(source=source):
                with self.assertRaises(ValueError):
                    equations.formula_xml(source)


def synthetic_slide(names):
    root = ET.Element("{" + NS["p"] + "}sld")
    tree = ET.SubElement(ET.SubElement(root, "{" + NS["p"] + "}cSld"), "{" + NS["p"] + "}spTree")
    ET.SubElement(tree, "{" + NS["p"] + "}nvGrpSpPr")
    ET.SubElement(tree, "{" + NS["p"] + "}grpSpPr")
    for index, (name, text) in enumerate(names.items(), 2):
        shape = ET.SubElement(tree, "{" + NS["p"] + "}sp")
        nv = ET.SubElement(shape, "{" + NS["p"] + "}nvSpPr")
        ET.SubElement(nv, "{" + NS["p"] + "}cNvPr", id=str(index), name=name, descr="original")
        ET.SubElement(nv, "{" + NS["p"] + "}cNvSpPr", txBox="1")
        ET.SubElement(nv, "{" + NS["p"] + "}nvPr")
        sppr = ET.SubElement(shape, "{" + NS["p"] + "}spPr")
        xfrm = ET.SubElement(sppr, "{" + NS["a"] + "}xfrm", rot="1200000")
        ET.SubElement(xfrm, "{" + NS["a"] + "}off", x="12345", y="67890")
        ET.SubElement(xfrm, "{" + NS["a"] + "}ext", cx="4000000", cy="900000")
        tx = ET.SubElement(shape, "{" + NS["p"] + "}txBody")
        ET.SubElement(tx, "{" + NS["a"] + "}bodyPr", anchor="ctr", lIns="1")
        ET.SubElement(tx, "{" + NS["a"] + "}lstStyle")
        p = ET.SubElement(tx, "{" + NS["a"] + "}p")
        ET.SubElement(p, "{" + NS["a"] + "}pPr", algn="ctr")
        run = ET.SubElement(p, "{" + NS["a"] + "}r")
        rp = ET.SubElement(run, "{" + NS["a"] + "}rPr", sz="2400", b="1")
        ET.SubElement(ET.SubElement(rp, "{" + NS["a"] + "}solidFill"), "{" + NS["a"] + "}srgbClr", val="123456")
        ET.SubElement(rp, "{" + NS["a"] + "}latin", typeface="Arial")
        ET.SubElement(run, "{" + NS["a"] + "}t").text = text
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


class AttachmentTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(equations, "finite-math implementation is missing")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.pptx = Path(self.temp.name) / "formula.pptx"
        self.slide = synthetic_slide({"eq1": "x_i^2", "literal": "plain words", "bad": "中文+x"})
        with ZipFile(self.pptx, "w", ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
            archive.writestr("ppt/slides/slide1.xml", self.slide)
            archive.writestr("ppt/media/untouched.bin", b"unaltered\x00payload")

    def read_slide(self):
        with ZipFile(self.pptx) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(archive.read("ppt/media/untouched.bin"), b"unaltered\x00payload")
            raw = archive.read("ppt/slides/slide1.xml")
        return raw, ET.fromstring(raw)

    def test_actual_officemath_choice_and_original_fallback_preserve_geometry(self):
        original = ET.fromstring(self.slide).find(".//p:sp", NS)
        result = equations.attach_equations(self.pptx, {"eq1": "x_i^2"})
        self.assertEqual(result, {"embedded": ["eq1"], "fallback": [], "warnings": []})
        raw, root = self.read_slide()
        wrapper = root.find(".//mc:AlternateContent", NS)
        choice = wrapper.find("mc:Choice", NS)
        self.assertEqual(choice.get("Requires"), "a14")
        self.assertIn(b"xmlns:a14=", raw)
        native = choice.find("p:sp", NS)
        fallback = wrapper.find("mc:Fallback/p:sp", NS)
        self.assertEqual(ET.tostring(fallback), ET.tostring(original))
        self.assertEqual(ET.tostring(native.find("p:spPr", NS)), ET.tostring(original.find("p:spPr", NS)))
        self.assertEqual(native.find("p:nvSpPr/p:cNvPr", NS).get("name"), "eq1")
        self.assertIsNotNone(native.find("p:txBody/a:p/a14:m/m:oMath/m:sSubSup", NS))
        self.assertEqual(native.findall(".//a:t", NS), [])
        self.assertGreater(len(native.findall(".//a:latin[@typeface='Cambria Math']", NS)), 0)
        self.assertEqual(native.find("p:txBody/a:bodyPr", NS).get("lIns"), "1")

    def test_idempotent_repeated_attachment_does_not_nest_or_rewrite_package(self):
        expected = equations.attach_equations(self.pptx, {"eq1": "x_i^2"})
        before = self.pptx.read_bytes()
        self.assertEqual(equations.attach_equations(self.pptx, {"eq1": "x_i^2"}), expected)
        self.assertEqual(self.pptx.read_bytes(), before)
        _, root = self.read_slide()
        self.assertEqual(len(root.findall(".//mc:AlternateContent", NS)), 1)

    def test_changed_formula_reuses_same_fallback_and_one_wrapper(self):
        equations.attach_equations(self.pptx, {"eq1": "x_i^2"})
        equations.attach_equations(self.pptx, {"eq1": r"\frac{a}{b}"})
        _, root = self.read_slide()
        self.assertEqual(len(root.findall(".//mc:AlternateContent", NS)), 1)
        self.assertIsNotNone(root.find(".//mc:Choice//m:f", NS))
        self.assertEqual(root.find(".//mc:Fallback//a:t", NS).text, "x_i^2")

    def test_unsupported_math_and_cjk_keep_editable_original_and_warn(self):
        before = self.pptx.read_bytes()
        result = equations.attach_equations(self.pptx, {"bad": "中文+x", "eq1": r"\begin{matrix}x\end{matrix}"})
        self.assertEqual(result["embedded"], [])
        self.assertEqual(result["fallback"], ["bad", "eq1"])
        self.assertEqual(len(result["warnings"]), 2)
        self.assertEqual(self.pptx.read_bytes(), before)
        _, root = self.read_slide()
        self.assertEqual(root.findall(".//mc:AlternateContent", NS), [])

    def test_missing_shape_warns_without_creating_a_shape(self):
        result = equations.attach_equations(self.pptx, {"missing": "x_i^2"})
        self.assertEqual(result["fallback"], ["missing"])
        self.assertEqual(result["embedded"], [])
        self.assertIn("missing", result["warnings"][0])
        _, root = self.read_slide()
        self.assertEqual(len(root.findall(".//p:sp", NS)), 3)

    def test_empty_mapping_is_byte_preserving(self):
        before = self.pptx.read_bytes()
        self.assertEqual(equations.attach_equations(self.pptx, {}), {"embedded": [], "fallback": [], "warnings": []})
        self.assertEqual(self.pptx.read_bytes(), before)

    def test_duplicate_target_names_fail_safely_with_warning(self):
        with ZipFile(self.pptx, "a") as archive:
            archive.writestr("ppt/slides/slide2.xml", self.slide)
        result = equations.attach_equations(self.pptx, {"eq1": "x"})
        self.assertEqual(result["embedded"], [])
        self.assertEqual(result["fallback"], ["eq1"])
        self.assertIn("ambiguous", result["warnings"][0].lower())

    def test_font_and_paragraph_defaults_keep_schema_child_order(self):
        root = ET.fromstring(self.slide)
        paragraph_properties = root.find(".//p:sp/p:txBody/a:p/a:pPr", NS)
        ET.SubElement(paragraph_properties, "{" + NS["a"] + "}extLst")
        properties = root.find(".//p:sp/p:txBody/a:p/a:r/a:rPr", NS)
        ET.SubElement(properties, "{" + NS["a"] + "}hlinkClick", action="example")
        with ZipFile(self.pptx, "w", ZIP_DEFLATED) as archive:
            archive.writestr("ppt/slides/slide1.xml", ET.tostring(root))
            archive.writestr("ppt/media/untouched.bin", b"unaltered\x00payload")
        equations.attach_equations(self.pptx, {"eq1": "x"})
        _, root = self.read_slide()
        paragraph_properties = root.find(".//mc:Choice//a:pPr", NS)
        tags = [x.tag.rsplit("}", 1)[-1] for x in paragraph_properties]
        self.assertLess(tags.index("defRPr"), tags.index("extLst"))
        defaults = paragraph_properties.find("a:defRPr", NS)
        tags = [x.tag.rsplit("}", 1)[-1] for x in defaults]
        for name in ("latin", "ea", "cs"):
            self.assertLess(tags.index(name), tags.index("hlinkClick"))

    def test_prefixes_used_only_in_mc_attributes_survive_serialization(self):
        root = ET.fromstring(self.slide)
        root.set("{" + NS["mc"] + "}Ignorable", "p99")
        source = ET.tostring(root).replace(b"<p:sld ", b'<p:sld xmlns:p99="urn:newpath:reserved" ', 1)
        self.assertIn(b"urn:newpath:reserved", source)
        with ZipFile(self.pptx, "w", ZIP_DEFLATED) as archive:
            archive.writestr("ppt/slides/slide1.xml", source)
            archive.writestr("ppt/media/untouched.bin", b"unaltered\x00payload")
        equations.attach_equations(self.pptx, {"eq1": "x"})
        raw, _ = self.read_slide()
        self.assertIn(b'xmlns:p99="urn:newpath:reserved"', raw)


if __name__ == "__main__":
    unittest.main()
