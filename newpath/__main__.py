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
    args=parser.parse_args(argv)
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    try:
        from .controller import run
        data=json.loads(args.input.read_text(encoding='utf-8-sig')) if args.input else None
        result=run(args.work,source=args.source,data=data,retry_task=args.retry_task)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0
    except (ValueError,OSError,KeyError,TypeError,ImportError,RuntimeError,zipfile.BadZipFile) as exc:
        print(json.dumps({'kind':'error','message':str(exc),'work':str(args.work.resolve()),
            'instructions':'Keep the job and its returned files. Correct registration or resume the same directory; do not infer permission to generate again.'},ensure_ascii=False))
        return 2


if __name__=='__main__':
    raise SystemExit(main())
