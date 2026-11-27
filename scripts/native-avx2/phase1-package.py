#!/usr/bin/env python3
"""Exact artifact identity and same-run per-arm smoke receipts."""
import importlib.util
import json
import os
import sys
from pathlib import Path

def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name+".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

base = load("collect-verified")
phase = load("phase1-overlay")
GROUPS = phase.GROUPS
UPSTREAM = phase.UPSTREAM
OVERLAY = phase.LOCALIZATION


def names(g):
    if g not in GROUPS:
        raise ValueError("Unknown group")
    return [g+".exe",g+"-native-manifest.json",g+"-LICENSE",g+"-LICENSE-dictionary",g+".sha256"]


def verify(directory, g, commit):
    m = json.loads((directory/(g+"-native-manifest.json")).read_text(encoding="utf-8"))
    e = m["experiment"]
    if (m["source"]["commit"] != UPSTREAM or e["workflow_commit"] != commit or
            e["localization_commit"] != OVERLAY or e["configuration"] != GROUPS[g] or
            e["trace_enabled"] is not True or set(e["groups"]) != {g} or
            not base.HEX.fullmatch(e["groups"][g]["sha256"]) or
            not all(base.HEX.fullmatch(v) for v in e["evidence"].values()) or
            not {"aquery.json", "compiler-trace.json", "full-overlay.patch", "clang-version.txt", "lld-version.txt", "rustc-version.txt", "embed.json", "pe-imports.txt"} <= set(e["evidence"])):
        raise ValueError("Manifest mismatch: "+g)
    if (directory/(g+".sha256")).read_text().strip() != base.sha(directory/(g+".exe"))+"  "+g+".exe":
        raise ValueError("EXE checksum mismatch")
    return m


def manifest(g):
    d = Path("phase1-audit")
    f = Path("phase1-native/pi_natives.win32-x64-modern.node")
    if json.loads((d/"configuration.json").read_text()) != GROUPS[g]:
        raise ValueError("Build config mismatch")
    trace = json.loads((d/"compiler-trace.json").read_text())
    if trace["errors"] or trace["compiler_invocations"] < 1 or trace["modern_cpu"] != GROUPS[g]["cpu"]:
        raise ValueError("Invalid trace")
    m = {"schema": 2, "source":{"commit":UPSTREAM,"version":"19.2.1"},
         "experiment":{"workflow_commit":os.environ["GITHUB_SHA"],"localization_commit":OVERLAY,
                       "configuration":GROUPS[g],"trace_enabled":True,
                       "evidence":{p.name:base.sha(p) for p in sorted(d.iterdir()) if p.is_file()},
                       "groups":{g:{"sha256":base.sha(f),"size_bytes":f.stat().st_size,
                                    "file":f.name}}}}
    Path("phase1-native/manifest.json").write_text(json.dumps(m,indent=2)+"\n")


def prepare(g, repo, run, commit):
    pkg = base.exact(base.artifacts(repo,run),"phase1-candidate-"+g)
    target = Path("package-input")
    target.mkdir(exist_ok=False)
    base.download(repo,run,pkg,target,names(g))
    verify(target,g,commit)
    with open(os.environ["GITHUB_OUTPUT"],"a",encoding="utf-8") as out:
        out.write(f"id={pkg['id']}\ndigest={pkg['digest']}\n")


def collect(repo,run,commit):
    items = base.artifacts(repo,run)
    dist = Path("dist")
    dist.mkdir()
    receipts,missing = [],[]
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        for g in GROUPS:
            match = [a for a in items if a["name"]=="phase1-receipt-"+g and not a["expired"]]
            if not match:
                missing.append(g)
                continue
            receipt_dir = Path(t)/g
            base.download(repo,run,base.exact(items,"phase1-receipt-"+g),receipt_dir,["smoke-receipt.json"])
            q = json.loads((receipt_dir/"smoke-receipt.json").read_text(encoding="utf-8-sig"))
            pkg = base.exact(items,"phase1-candidate-"+g)
            if (q["group"]!=g or q["runId"]!=run or q["sourceSha"]!=commit or
                    q["artifactId"]!=str(pkg["id"]) or q["artifactDigest"]!=pkg["digest"] or
                    set(q["files"])!=set(names(g))):
                raise ValueError("Receipt identity mismatch: "+g)
            base.download(repo,run,pkg,dist,names(g))
            m = verify(dist,g,commit)
            if any(base.sha(dist/n)!=q["files"][n] for n in names(g)):
                raise ValueError("Receipt file mismatch: "+g)
            if GROUPS[g]["local_only"]:
                if q["status"]!="local-hardware-required" or q["loadedAddonSha256"] is not None:
                    raise ValueError("Bad hardware status")
            elif q["status"]!="windows-smoke-passed" or q["loadedAddonSha256"]!=m["experiment"]["groups"][g]["sha256"]:
                raise ValueError("Smoke hash mismatch")
            receipts.append(q)
    (dist/"phase1-status.json").write_text(json.dumps({"run":run,"sha":commit,"missing":missing,"receipts":receipts},indent=2)+"\n")
    (dist/"SHA256SUMS.txt").write_text("".join(base.sha(dist/(q["group"]+".exe"))+"  "+q["group"]+".exe\n" for q in receipts))
    print("Collected:",[q["group"] for q in receipts],"Missing:",missing)
    if not receipts:
        raise ValueError("No eligible packages")


if __name__=="__main__":
    mode=sys.argv[1]
    if mode=="manifest":
        manifest(os.environ["GROUP"])
    elif mode=="prepare":
        prepare(os.environ["GROUP"],*(os.environ[k] for k in ("GITHUB_REPOSITORY","GITHUB_RUN_ID","GITHUB_SHA")))
    elif mode=="collect":
        collect(*(os.environ[k] for k in ("GITHUB_REPOSITORY","GITHUB_RUN_ID","GITHUB_SHA")))
    else:
        raise ValueError("Unknown mode")
