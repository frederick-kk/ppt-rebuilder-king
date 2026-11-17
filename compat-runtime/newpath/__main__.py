"""Single JSON entry point for a host assistant with real image tools."""
import argparse
import json
import os
from pathlib import Path
import sys
import zipfile


def _compact_done(result,work):
    """Project terminal stdout while keeping the complete receipt on disk."""
    if result.get('kind')!='done':
        return result
    scalars=(str,bool,int,float,type(None))
    compact={key:value for key,value in result.items() if isinstance(value,scalars)}
    for key in ('skipped','delivery_summary'):
        if key in result:
            compact[key]=result[key]
    warnings=result.get('warnings',[])
    if isinstance(warnings,list):
        brief_warnings=[]
        seen=set()
        for warning in warnings:
            if isinstance(warning,dict):
                brief={key:value for key,value in warning.items() if isinstance(value,scalars)}
            else:
                brief=warning if isinstance(warning,scalars) else {}
            identity=json.dumps(brief,ensure_ascii=False,sort_keys=True,separators=(',',':'))
            if identity not in seen:
                seen.add(identity)
                brief_warnings.append(brief)
        compact.update(warnings=brief_warnings,warning_count=len(warnings))
    else:
        compact.update(warnings=warnings,warning_count=None)
    compact['receipt_file']=str((Path(work)/'receipt.json').resolve())
    return compact


def _action_file_path(args):
    """Keep optional command output outside runtime and authoritative inputs."""
    lexical=Path(os.path.abspath(args.action_file))
    destination=lexical.resolve()
    roots=(Path(os.path.abspath(args.work)),args.work.resolve(),
           Path(os.path.abspath(__file__)).parent.parent,Path(__file__).resolve().parent.parent)
    if any(path.is_relative_to(root) for path in (lexical,destination) for root in roots):
        raise ValueError('The action file must be outside the job and runtime directories.')
    if destination.is_dir():
        raise ValueError('The action file must name an external file, not a directory.')
    protected=[path for path in (args.source,args.input,args.diagnostics) if path is not None]
    marker=args.work/'initialization.json'
    if marker.is_file():
        try:
            saved=json.loads(marker.read_text(encoding='utf-8-sig'))
        except (ValueError,OSError):
            saved={}
        original=saved.get('input_path') if isinstance(saved,dict) else None
        if isinstance(original,str) and original:
            protected.append(Path(original))
    for original in protected:
        if (destination==original.resolve()
                or (destination.exists() and original.exists() and destination.samefile(original))):
            raise ValueError('The action file cannot replace a source, input, or diagnostics output file.')
    return destination


def _save_action(result,action_file):
    if action_file is None:
        return result
    from .storage import write_json
    action={**result,'action_file':str(action_file)}
    write_json(action_file,action)
    return action


def _file_action_summary(action, work):
    """Keep complete templates in the action file and current observations visible."""
    if action.get('kind') == 'done':
        return _compact_done(action, work)
    summary = dict(action)
    changed = False
    if action.get('kind') == 'generate' and isinstance(action.get('jobs'), list):
        jobs = []
        for task in action['jobs']:
            if isinstance(task, dict) and 'begin_template' in task:
                task = dict(task)
                del task['begin_template']
                task['begin_template_file'] = action['action_file']
                changed = True
            jobs.append(task)
        summary['jobs'] = jobs
    if (action.get('kind') == 'invoke_once'
            and isinstance(action.get('request_file'), str)
            and action['request_file'] and 'request' in action):
        del summary['request']
        changed = True
    if changed:
        summary['action_projection'] = 'file-backed-current-action/1'
        summary['full_action_file'] = action['action_file']
    return summary


def main(argv=None):
    parser=argparse.ArgumentParser(prog='python -m newpath')
    parser.add_argument('--work',required=True,type=Path)
    inputs=parser.add_mutually_exclusive_group()
    inputs.add_argument('--source',type=Path)
    inputs.add_argument('--input',type=Path)
    inputs.add_argument('--retry-task',help='Only after the user explicitly requests a new invocation')
    inputs.add_argument('--diagnostics',type=Path,metavar='OUTPUT_ZIP',
                        help='Read only this exact existing --work directory into a new external ZIP')
    parser.add_argument('--retry-authorization',help='Quote the explicit user request authorizing this failed-task retry; recorded caller assertion, not verified identity')
    parser.add_argument('--expected-package',help='Require this exact package identity; a mismatch stops before generation')
    parser.add_argument('--compact',action='store_true',help='Print a brief done response while preserving the full receipt')
    parser.add_argument('--source-first',action='store_true',
                        help='Freeze the source description before generation and use source-budgeted boards')
    parser.add_argument('--sequential-layers',action='store_true',
                        help='Generate background, then full foreground, before describing the page')
    parser.add_argument('--foreground-components', action='store_true',
                        help='Sample every artwork member from the generated foreground with --sequential-layers')
    parser.add_argument('--keyed-materials', action='store_true',
                        help='Derive foreground reference alpha from its safe solid-colour field')
    parser.add_argument('--direct-source-boards', action='store_true',
                        help='Use original-source component references with --source-first')
    parser.add_argument('--isolated-title-reference', action='store_true',
                        help='Reference the primary title through its observed source-part regions')
    parser.add_argument('--action-file',type=Path,
                        help='Atomically save the current JSON action to an external file')
    parser.add_argument('--file-actions', action='store_true',
                        help='Print brief action output when the complete action is saved with --action-file')
    args=parser.parse_args(argv)
    if args.diagnostics is not None and args.retry_authorization is not None:
        parser.error('--diagnostics cannot be combined with --retry-authorization')
    if args.sequential_layers and (args.source_first or args.direct_source_boards or args.isolated_title_reference):
        parser.error('--sequential-layers cannot be combined with source-first strategies')
    if args.foreground_components and not args.sequential_layers:
        parser.error('--foreground-components requires --sequential-layers')
    if args.keyed_materials and not (args.foreground_components and args.sequential_layers):
        parser.error('--keyed-materials requires --sequential-layers --foreground-components')
    if args.direct_source_boards and not args.source_first:
        parser.error('--direct-source-boards requires --source-first')
    if args.isolated_title_reference and not args.source_first:
        parser.error('--isolated-title-reference requires --source-first')
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    action_file=None
    try:
        if args.action_file is not None:
            action_file=_action_file_path(args)
        if args.diagnostics is not None:
            from .diagnostics import export_diagnostics
            result=export_diagnostics(args.work,args.diagnostics)
            result=_save_action(result,action_file)
            print(json.dumps(result,ensure_ascii=False,indent=2))
            return 0
        from .controller import run
        data=json.loads(args.input.read_text(encoding='utf-8-sig')) if args.input else None
        options={}
        if args.source_first:
            from .controller import SOURCE_LAYOUT_POLICY
            from .boardplan import SOURCE_BUDGET_POLICY
            options.update(source_layout_policy=SOURCE_LAYOUT_POLICY,
                           board_sampling_policy=SOURCE_BUDGET_POLICY)
        if args.sequential_layers:
            from .controller import SEQUENTIAL_LAYER_ORDER
            options['layer_generation_order'] = SEQUENTIAL_LAYER_ORDER
        if args.foreground_components:
            from .controller import FOREGROUND_COMPONENT_POLICY
            options['foreground_component_policy'] = FOREGROUND_COMPONENT_POLICY
        if args.keyed_materials:
            from .controller import KEYED_MATERIAL_POLICY
            options['keyed_material_policy'] = KEYED_MATERIAL_POLICY
        if args.direct_source_boards:
            from .controller import DIRECT_SOURCE_BOARD_POLICY
            options['source_board_reference_policy'] = DIRECT_SOURCE_BOARD_POLICY
        if args.isolated_title_reference:
            from .source import TITLE_REFERENCE_POLICY
            options['primary_source_reference_policy'] = TITLE_REFERENCE_POLICY
        if args.source is not None and not (args.work/'state.json').exists():
            # Only the supported new-job CLI opts into the new protocol.
            # Saved legacy jobs are never silently migrated on resume.
            from .execution import MODE, POST_RETURN_PROMPT_POLICY
            from .version import PACKAGE_ID, validate_work_directory
            validate_work_directory(args.work)
            options.update(execution_policy=MODE,expected_package=args.expected_package or PACKAGE_ID)
            if not (args.work/'initialization.json').exists():
                from .binding import DELIVERY_POLICY
                from .recovery import MODE as RECOVERY_POLICY
                if args.keyed_materials:
                    from .recovery import MODE_V2 as RECOVERY_POLICY
                options['post_return_prompt_policy']=POST_RETURN_PROMPT_POLICY
                options['delivery_policy']=DELIVERY_POLICY
                options['recovery_policy']=RECOVERY_POLICY
        elif args.expected_package is not None:
            options['expected_package']=args.expected_package
        result=run(args.work,source=args.source,data=data,retry_task=args.retry_task,
                   retry_authorization=args.retry_authorization,**options)
        if args.file_actions and action_file is not None:
            result = _save_action(result, action_file)
            result = _file_action_summary(result, args.work)
        else:
            if args.compact:
                result=_compact_done(result,args.work)
            result=_save_action(result,action_file)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0
    except (ValueError,OSError,KeyError,TypeError,ImportError,RuntimeError,zipfile.BadZipFile) as exc:
        result={'kind':'error','message':str(exc),'work':str(args.work.resolve()),
            'instructions':'Keep the job and its returned files. Correct registration or resume the same directory; do not infer permission to generate again.'}
        try:
            result=_save_action(result,action_file)
        except (ValueError,OSError,KeyError,TypeError,ImportError,RuntimeError,zipfile.BadZipFile) as save_error:
            result.update(action_file=None,action_file_error=str(save_error))
        print(json.dumps(result,ensure_ascii=False))
        return 2


if __name__=='__main__':
    raise SystemExit(main())
