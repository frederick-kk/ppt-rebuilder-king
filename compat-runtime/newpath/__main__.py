"""Single JSON entry point for a host assistant with real image tools."""
import argparse
import json
from pathlib import Path
import sys
import zipfile


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
    args=parser.parse_args(argv)
    if args.diagnostics is not None and args.retry_authorization is not None:
        parser.error('--diagnostics cannot be combined with --retry-authorization')
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    try:
        if args.diagnostics is not None:
            from .diagnostics import export_diagnostics
            result=export_diagnostics(args.work,args.diagnostics)
            print(json.dumps(result,ensure_ascii=False,indent=2))
            return 0
        from .controller import run
        data=json.loads(args.input.read_text(encoding='utf-8-sig')) if args.input else None
        options={}
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
                options['post_return_prompt_policy']=POST_RETURN_PROMPT_POLICY
                options['delivery_policy']=DELIVERY_POLICY
                options['recovery_policy']=RECOVERY_POLICY
        elif args.expected_package is not None:
            options['expected_package']=args.expected_package
        result=run(args.work,source=args.source,data=data,retry_task=args.retry_task,
                   retry_authorization=args.retry_authorization,**options)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0
    except (ValueError,OSError,KeyError,TypeError,ImportError,RuntimeError,zipfile.BadZipFile) as exc:
        print(json.dumps({'kind':'error','message':str(exc),'work':str(args.work.resolve()),
            'instructions':'Keep the job and its returned files. Correct registration or resume the same directory; do not infer permission to generate again.'},ensure_ascii=False))
        return 2


if __name__=='__main__':
    raise SystemExit(main())
