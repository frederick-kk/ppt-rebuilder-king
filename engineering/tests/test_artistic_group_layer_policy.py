"""Complete page-writing groups in the opted-in decorative-container policy."""
import ast
import copy
import hashlib
import importlib.util
from pathlib import Path
import sys
import unittest

sys.dont_write_bytecode = True
PACKAGE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PACKAGE / 'compat-runtime'))
from newpath import prompts
from newpath.native import MODE
from test_decorative_background_policy import OLD_PAYLOADS


class ArtisticGroupLayerPolicy(unittest.TestCase):
    def brief(self, role, mode=MODE):
        return prompts.layer_brief(role, {'offset': [0, 0], 'resized': [1919, 1080]},
                                   background_mode=mode, decorative_background=True)

    def test_eight_legacy_payloads_remain_exact_for_default_and_false(self):
        for transform, role, mode, expected in OLD_PAYLOADS:
            for options in ({}, {'decorative_background': False}):
                with self.subTest(transform=transform, role=role, mode=mode, options=options):
                    result = prompts.layer_brief(role, transform, background_mode=mode, **options)
                    self.assertEqual(hashlib.sha256(result.encode()).hexdigest(), expected)

    def test_new_policy_assigns_writing_associated_arcs_and_backing_to_complete_group(self):
        for role in ('background', 'foreground'):
            for mode in (None, MODE):
                with self.subTest(role=role, mode=mode):
                    result = self.brief(role, mode)
                    self.assertIn('complete foreground groups', result)
                    self.assertIn('connected or immediately adjacent arcs', result)
                    self.assertIn('painted brushstrokes and their own backing', result)
                    self.assertIn('belong to the source writing group', result)

    def test_background_removes_entire_writing_group_with_its_arcs_strokes_and_backing(self):
        for mode in (None, MODE):
            result = self.brief('background', mode)
            task = result.split('Background task: ', 1)[1]
            self.assertIn('complete artistic writing and brush-stroke title labels', task)
            self.assertIn('together with their associated arcs, strokes and backing', task)
            self.assertIn('Keep the integrated scene and the empty decorative paper bases', task)

    def test_foreground_keeps_complete_writing_and_label_backing_without_fixed_containers(self):
        for mode in (None, MODE):
            result = self.brief('foreground', mode)
            task = result.split('Foreground task: ', 1)[1]
            self.assertIn('complete artistic writing and brush-stroke title labels', task)
            self.assertIn('including their associated arcs, strokes and backing', task)
            self.assertIn('all decorative containers assigned to background', task)

    def test_independent_cloud_corner_ornaments_ribbons_and_scenery_stay_background(self):
        for role in ('background', 'foreground'):
            result = self.brief(role)
            self.assertIn('Independent corner or cloud ornaments, page-edge ribbons and integrated scenery', result)
            self.assertIn('retain their background assignment', result)

    def test_complete_content_images_and_image_internal_writing_keep_existing_contract(self):
        for role in ('background', 'foreground'):
            result = self.brief(role)
            self.assertIn(prompts.EMBEDDED_IMAGE_POLICY, result)
            self.assertIn('page writing outside independent content images', result)

    def test_native_and_chroma_modes_keep_their_existing_output_substrate(self):
        native = self.brief('foreground', MODE)
        chroma = self.brief('foreground', None)
        self.assertIn('one transparent PNG', native)
        self.assertIn('alpha 0', native)
        self.assertNotIn('solid magenta', native)
        self.assertIn('one opaque PNG', chroma)
        self.assertIn('uniform RGB(255,0,255)', chroma)
        for mode in (None, MODE):
            self.assertIn('one opaque PNG', self.brief('background', mode))

    def test_board_function_source_matches_r10(self):
        old_path = PACKAGE.parent / 'source-r10/compat-runtime/newpath/prompts.py'
        if not old_path.is_file():
            self.skipTest('The retained r10 source fixture is unavailable outside this audit checkout')
        old_text = old_path.read_text(encoding='utf-8')
        new_text = (PACKAGE / 'compat-runtime/newpath/prompts.py').read_text(encoding='utf-8')
        old = {node.name: node for node in ast.parse(old_text).body if isinstance(node, ast.FunctionDef)}
        new = {node.name: node for node in ast.parse(new_text).body if isinstance(node, ast.FunctionDef)}
        legacy = copy.deepcopy(new['board_brief'])
        direct_guard = legacy.body.pop(0)
        self.assertIsInstance(direct_guard, ast.If)
        self.assertEqual(ast.dump(direct_guard.test), ast.dump(ast.parse(
            "board.get('source_board_reference_policy') is not None", mode='eval').body))
        self.assertEqual(direct_guard.orelse, [])
        self.assertEqual(len(direct_guard.body), 1)
        self.assertIsInstance(direct_guard.body[0], ast.Return)
        self.assertEqual(ast.dump(direct_guard.body[0].value), ast.dump(ast.parse(
            '_source_board_brief(board)', mode='eval').body))
        guard = legacy.body.pop(0)
        self.assertIsInstance(guard, ast.If)
        self.assertEqual(ast.dump(guard.test), ast.dump(ast.parse(
            "board.get('board_reference_policy') is not None", mode='eval').body))
        self.assertEqual(guard.orelse, [])
        self.assertEqual(len(guard.body), 1)
        self.assertIsInstance(guard.body[0], ast.Return)
        self.assertEqual(ast.dump(guard.body[0].value), ast.dump(ast.parse(
            '_owned_image_board_brief(board)', mode='eval').body))
        self.assertEqual(ast.dump(legacy), ast.dump(old['board_brief']))
        dispatch = '    ' + ast.get_source_segment(new_text, guard) + '\n'
        direct_dispatch = '    ' + ast.get_source_segment(new_text, direct_guard) + '\n'
        self.assertEqual(ast.get_source_segment(new_text, new['board_brief'])
                         .replace(direct_dispatch, '', 1).replace(dispatch, '', 1),
                         ast.get_source_segment(old_text, old['board_brief']))

    def test_legacy_wordart_ast_and_payload_match_r15_except_optional_source_detail(self):
        old_path = PACKAGE.parent / 'source-r15/compat-runtime/newpath/prompts.py'
        if not old_path.is_file():
            self.skipTest('The retained r15 source fixture is unavailable outside this audit checkout')
        old = next(node for node in ast.parse(old_path.read_text(encoding='utf-8')).body
                   if isinstance(node, ast.FunctionDef) and node.name == 'wordart_brief')
        current_path = PACKAGE / 'compat-runtime/newpath/prompts.py'
        current = next(node for node in ast.parse(current_path.read_text(encoding='utf-8')).body
                       if isinstance(node, ast.FunctionDef) and node.name == 'wordart_brief')
        legacy = copy.deepcopy(current)
        self.assertEqual(legacy.args.kwonlyargs[-1].arg, 'source_detail')
        self.assertIsInstance(legacy.args.kw_defaults[-1], ast.Constant)
        self.assertIsNone(legacy.args.kw_defaults[-1].value)
        legacy.args.kwonlyargs.pop()
        legacy.args.kw_defaults.pop()
        guard = legacy.body.pop(0)
        self.assertIsInstance(guard, ast.If)
        self.assertEqual(ast.dump(guard.test), ast.dump(ast.parse('source_detail is not None', mode='eval').body))
        self.assertEqual(guard.orelse, [])
        self.assertEqual(ast.dump(legacy), ast.dump(old))
        spec = importlib.util.spec_from_file_location('newpath.r15_artistic_wordart_protection', old_path)
        previous = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(previous)
        member = {'id': 'title', 'box': [100, 10, 900, 150], 'lettering': '完整标题！',
                  'description': 'Original coloured ink, own arc and original white outlines.'}
        for sample in (member, {**member, 'lettering': '第一行\n第二行'},
                       {**member, 'box': [0.5, 5.5, 800.25, 120.5], 'description': 'Detached strokes.'}):
            with self.subTest(member=sample):
                expected = previous.wordart_brief(sample)
                self.assertEqual(prompts.wordart_brief(sample), expected)
                self.assertEqual(prompts.wordart_brief(sample, source_detail=None), expected)

    def test_legacy_layer_dispatch_ast_matches_r10_except_the_optional_allocation_branch(self):
        old_path = PACKAGE.parent / 'source-r10/compat-runtime/newpath/prompts.py'
        if not old_path.is_file():
            self.skipTest('The retained r10 source fixture is unavailable outside this audit checkout')
        old = next(node for node in ast.parse(old_path.read_text(encoding='utf-8')).body
                   if isinstance(node, ast.FunctionDef) and node.name == 'layer_brief')
        current_path = PACKAGE / 'compat-runtime/newpath/prompts.py'
        current = next(node for node in ast.parse(current_path.read_text(encoding='utf-8')).body
                       if isinstance(node, ast.FunctionDef) and node.name == 'layer_brief')
        legacy = copy.deepcopy(current)
        self.assertEqual(legacy.args.kwonlyargs[-1].arg, 'source_layer_scope')
        self.assertIsInstance(legacy.args.kw_defaults[-1], ast.Constant)
        self.assertIsNone(legacy.args.kw_defaults[-1].value)
        legacy.args.kwonlyargs.pop()
        legacy.args.kw_defaults.pop()
        scope_guard = legacy.body.pop(0)
        self.assertIsInstance(scope_guard, ast.If)
        self.assertEqual(ast.dump(scope_guard.test),
                         ast.dump(ast.parse('source_layer_scope is not None', mode='eval').body))
        self.assertEqual(scope_guard.orelse, [])
        self.assertEqual(len(scope_guard.body), 1)
        self.assertIsInstance(scope_guard.body[0], ast.Return)
        expected_dispatch = ast.parse(
            '_scoped_layer_brief(role, transform, background_mode, decorative_background, '
            'scene_allocation, source_layer_scope)', mode='eval').body
        self.assertEqual(ast.dump(scope_guard.body[0].value), ast.dump(expected_dispatch))
        self.assertEqual(legacy.args.kwonlyargs[-1].arg, 'scene_allocation')
        self.assertIsInstance(legacy.args.kw_defaults[-1], ast.Constant)
        self.assertIsNone(legacy.args.kw_defaults[-1].value)
        legacy.args.kwonlyargs.pop()
        legacy.args.kw_defaults.pop()
        guard = legacy.body.pop(0)
        self.assertIsInstance(guard, ast.If)
        self.assertEqual(ast.dump(guard.test), ast.dump(ast.parse('scene_allocation is not None', mode='eval').body))
        self.assertEqual(guard.orelse, [])
        self.assertEqual(ast.dump(legacy), ast.dump(old))

    def test_default_and_none_allocation_keep_all_approved_r14_layer_payloads_exact(self):
        old_path = PACKAGE.parent / 'source-r14/compat-runtime/newpath/prompts.py'
        if not old_path.is_file():
            self.skipTest('The retained r14 source fixture is unavailable outside this audit checkout')
        spec = importlib.util.spec_from_file_location('newpath.r14_artistic_dispatch_protection', old_path)
        old = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(old)
        for transform, role, mode, _ in OLD_PAYLOADS:
            for options in ({}, {'decorative_background': False}, {'decorative_background': True}):
                with self.subTest(transform=transform, role=role, mode=mode, options=options):
                    expected = old.layer_brief(role, transform, background_mode=mode, **options)
                    self.assertEqual(prompts.layer_brief(role, transform, background_mode=mode, **options), expected)
                    self.assertEqual(prompts.layer_brief(role, transform, background_mode=mode,
                                                        scene_allocation=None, **options), expected)
        for role in ('background', 'foreground'):
            for options in ({}, {'decorative_background': False}, {'decorative_background': True}):
                with self.subTest(role=role, options=options):
                    with self.assertRaises(ValueError) as prior:
                        old.layer_brief(role, {'offset': [0, 0], 'resized': [1919, 1080]},
                                        background_mode='unknown', **options)
                    with self.assertRaises(ValueError) as current:
                        prompts.layer_brief(role, {'offset': [0, 0], 'resized': [1919, 1080]},
                                            background_mode='unknown', scene_allocation=None, **options)
                    self.assertEqual(str(current.exception), str(prior.exception))


if __name__ == '__main__':
    unittest.main()
