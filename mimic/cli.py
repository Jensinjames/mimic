"""mimic CLI — capture apps, generate clients, and run a sandboxed code agent."""
from __future__ import annotations

import argparse
import os
import re
import shutil
import socket
import subprocess
import sys

from . import agent, codegen, unpin
from .sources import har, mitm


def _openai_sdk_ready():
    try:
        from openai import OpenAI  # noqa: F401
    except (ImportError, AttributeError):
        return False
    return True


def _mitm_and_flows():
    m = mitm.Mitm()
    return m, m.flows()


def _flows_from_args(args):
    if getattr(args, "har", None):
        return None, har.load(args.har)
    return _mitm_and_flows()


def _endpoints_from_args(args, m, flows):
    if getattr(args, "har", None):
        return har.endpoints(args.har, args.host)
    return mitm.endpoints(m, flows, args.host)


def _lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "<this-machine-ip>"
    finally:
        s.close()


def _mitmweb_cmd():
    if shutil.which("mitmweb"):
        return ["mitmweb"]
    if shutil.which("uvx"):
        return ["uvx", "--from", "mitmproxy", "mitmweb"]
    return None


def cmd_record(args):
    ip = _lan_ip()
    print(f"iPhone proxy: {ip}:8080\nInstall/trust the mitmproxy profile, use the app, then run `mimic hosts`.")
    cmd = _mitmweb_cmd()
    if not cmd:
        sys.exit("no proxy launcher found — install uv or mitmproxy")
    subprocess.run(cmd, check=False)


def cmd_doctor(args):
    ok = True
    def required(name, present, fix):
        nonlocal ok
        print(f"  [{'ok ' if present else 'MISSING'}] {name}")
        if not present:
            ok = False
            print(f"       -> {fix}")
    required("OpenAI SDK", _openai_sdk_ready(), "reinstall mimic-client")
    required("OPENAI_API_KEY", bool(os.environ.get("OPENAI_API_KEY", "").strip()), "export OPENAI_API_KEY=...")
    print(f"  [{'ok ' if _mitmweb_cmd() else '  -'}] proxy launcher (optional with HAR)")
    sys.exit(0 if ok else 1)


def cmd_hosts(args):
    _, flows = _flows_from_args(args)
    rows = mitm.hosts(flows)
    if not rows:
        sys.exit("no traffic found")
    for host, n in rows:
        print(f"{n:>9}  {host}")


def cmd_learn(args):
    m, flows = _flows_from_args(args)
    eps = _endpoints_from_args(args, m, flows)
    if not eps:
        sys.exit(f"no requests to {args.host} found")
    for e in eps:
        print(f"{e['method']:5s} {e['path']} -> {e['status']}")


def cmd_gen(args):
    m, flows = _flows_from_args(args)
    eps = _endpoints_from_args(args, m, flows)
    if not eps:
        sys.exit(f"no requests to {args.host} found")
    if args.prompt_only:
        print(codegen.build_prompt(args.host, eps))
        return
    out = args.out or _default_out(args.host)
    try:
        source = codegen.generate(args.host, eps, model=args.model)
    except RuntimeError as exc:
        sys.exit(str(exc))
    with open(out, "w", encoding="utf-8") as f:
        f.write(source)
    cls = _class_name(source)
    module = os.path.splitext(os.path.basename(out))[0]
    print(f"wrote {out}\nfrom {module} import {cls or 'Client'}")


def cmd_agent(args):
    try:
        result = agent.run_agent(
            args.task,
            path=args.path,
            model=args.model,
            container_size=args.container_size,
            out_dir=args.out_dir,
            apply=args.apply,
        )
    except (RuntimeError, ValueError) as exc:
        sys.exit(str(exc))
    if result.output_text.strip():
        print(result.output_text.strip())
    print(f"report: {result.report_path}\npatch: {result.patch_path}\nworkspace: {result.workspace_zip_path}")
    if args.apply:
        print(f"applied: {'yes' if result.patch_applied else 'no changes'}")


def _default_out(host):
    stem = re.sub(r"[^a-z0-9]+", "_", host.split(".")[0].lower()).strip("_")
    return f"{stem or 'app'}_client.py"


def _class_name(source):
    m = re.search(r"class\s+(\w+)\s*\(", source)
    return m.group(1) if m else None


def main(argv=None):
    p = argparse.ArgumentParser(prog="mimic", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("record").set_defaults(func=cmd_record)
    sub.add_parser("doctor").set_defaults(func=cmd_doctor)

    hp = sub.add_parser("hosts")
    hp.add_argument("--har")
    hp.set_defaults(func=cmd_hosts)

    lp = sub.add_parser("learn")
    lp.add_argument("host")
    lp.add_argument("--har")
    lp.set_defaults(func=cmd_learn)

    gp = sub.add_parser("gen")
    gp.add_argument("host")
    gp.add_argument("-o", "--out")
    gp.add_argument("--model", default=codegen.DEFAULT_MODEL)
    gp.add_argument("--prompt-only", action="store_true")
    gp.add_argument("--har")
    gp.set_defaults(func=cmd_gen)

    ap = sub.add_parser("agent", help="run a software task in an OpenAI-hosted sandbox")
    ap.add_argument("task")
    ap.add_argument("--path", default=".")
    ap.add_argument("--model", default=agent.DEFAULT_MODEL)
    ap.add_argument("--container-size", choices=["small", "medium", "large"], default=agent.DEFAULT_CONTAINER_SIZE)
    ap.add_argument("--out-dir", default=".mimic-agent")
    ap.add_argument("--apply", action="store_true")
    ap.set_defaults(func=cmd_agent)

    up = sub.add_parser("unpin")
    up.add_argument("target")
    up.add_argument("--ca")
    up.add_argument("--proxy-host")
    up.add_argument("--workdir")
    up.add_argument("--codesign")
    up.set_defaults(func=unpin.cmd_unpin)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
