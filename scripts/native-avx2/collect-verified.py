"""Collect only artifacts whose immutable ID and bytes were accepted by Windows smoke."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import subprocess
import tempfile
import zipfile

GROUPS = ("A0", "A1", "B", "C")
HEX = re.compile(r"[a-f0-9]{64}")

def sha(path):
    with path.open("rb") as f:
        h = hashlib.sha256()
        for block in iter(lambda: f.read(1024*1024), b""):
            h.update(block)
        return h.hexdigest()

def api(path):
    return json.loads(subprocess.check_output(["gh", "api", path], encoding="utf-8"))

def exact(items, name):
    matches = [a for a in items if a["name"] == name and not a["expired"]]
    if len(matches) != 1:
        raise ValueError("Expected one artifact: " + name)
    return matches[0]

def download(repo, run, artifact, target, names):
    aid = str(artifact["id"])
    digest = artifact.get("digest", "")
    if not aid.isdecimal() or not digest.startswith("sha256:") or not HEX.fullmatch(digest[7:]):
        raise ValueError("Invalid artifact ID or digest")
    meta = api(f"repos/{repo}/actions/artifacts/{aid}")
    if (meta["name"] != artifact["name"] or meta["digest"] != digest
            or meta["expired"] or str(meta["workflow_run"]["id"]) != run):
        raise ValueError("Artifact identity mismatch")
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp)/"artifact.zip"
        subprocess.run(["curl","--fail","--silent","--show-error","--location","--retry","3",
                        "-H","Authorization: Bearer "+os.environ["GH_TOKEN"],
                        "-H","Accept: application/vnd.github+json",
                        f"https://api.github.com/repos/{repo}/actions/artifacts/{aid}/zip",
                        "-o",str(archive)],check=True)
        if sha(archive) != digest[7:]:
            raise ValueError("Artifact ZIP hash mismatch")
        with zipfile.ZipFile(archive) as z:
            members = z.infolist()
            if len(members) != len(names) or {i.filename for i in members} != set(names):
                raise ValueError("Unexpected or duplicate package files")
            target.mkdir(parents=True, exist_ok=True)
            for i in members:
                if i.is_dir() or (i.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError("Not a regular package member")
                with z.open(i) as src, (target/i.filename).open("xb") as dest:
                    shutil.copyfileobj(src,dest)

# Explicit, immutable recovery exception. No arbitrary historical run is accepted.
RECOVERY_RUN = "45921779633"
RECOVERY_SHA = "e191017a40bf5134202a71781964dfbae3718a6d"
RECOVERY_JOBS = {
    140253950335: "Build A0 / A1 / B / C (Windows x64)",
    140276585554: "package / package (A1, A1-self-built-baseline, baseline)",
    140276585557: "package / package (C, C-v3-thin-cgu1, modern)",
    140276585591: "package / package (A0, A0-official-baseline, baseline)",
    140276585625: "package / package (B, B-v3-thin-cgu16, modern)",
}
RECOVERY_PINS = {
    "experiment-exe-A0": (15166819802, "791d6bf60f1d9c5b1077f3963fc0d8c6e35072a46739fab7dc5ed9f679382c47"),
    "experiment-exe-A1": (15167648330, "d6952cd4396cdfcb1e97b1dfbaefabf1d889530d9ac922a5b38093e7a85e1d33"),
    "experiment-exe-B": (15166944534, "9b7de716620de2f5ef04b713aaf73a0a9e8606a6c2a4b24b8b94a0177bb50906"),
    "experiment-exe-C": (15167303696, "6d51a4e60ba49ecf2f6443aa2c0e8d5ea3076303f4a4fa89e192de4d476dc3aa"),
    "native-avx2-v19.2.1-45921779633": (15167038786, "658b7db337e9321fadb12d0b9d4fac316f82d5cf855afe0659da838a06946334"),
}
UPSTREAM = "b55cd1bb664d8ab46ddc77f2a34eb47555dbf1ac"
OVERLAY = "ac1616560eb7d04d2a58afe97dcec88f310a1ad8"


def artifacts(repo, run):
    pages = json.loads(subprocess.check_output(
        ["gh", "api", "--paginate", "--slurp", f"repos/{repo}/actions/runs/{run}/artifacts?per_page=100"],
        encoding="utf-8"))
    return [a for p in pages for a in p["artifacts"]]


def package_names(group):
    if group not in GROUPS:
        raise ValueError("Unknown group")
    return [group+".exe", group+"-native-manifest.json", group+"-LICENSE", group+"-LICENSE-dictionary", group+".sha256"]


def pinned_items(items):
    for name, (aid, digest) in RECOVERY_PINS.items():
        a = exact(items, name)
        if a["id"] != aid or a["digest"] != "sha256:" + digest:
            raise ValueError("Recovery artifact does not match immutable pin: " + name)
    return [exact(items, "experiment-exe-"+g) for g in GROUPS]


def producer(repo, run, commit):
    recovery = os.environ.get("RECOVER_SMOKE", "false")
    if recovery not in ("true", "false"):
        raise ValueError("Invalid recovery mode")
    if recovery == "false":
        return run, commit, artifacts(repo, run), False
    if repo != "WhiCCX0/oh-my-pi":
        raise ValueError("Recovery is limited to the audited repository")
    old = api(f"repos/{repo}/actions/runs/{RECOVERY_RUN}")
    if (str(old["id"]) != RECOVERY_RUN or old["head_sha"] != RECOVERY_SHA
            or old["head_branch"] != "experiment/native-avx2"
            or old["path"] != ".github/workflows/native-avx2-experiment.yml"
            or old["status"] != "completed" or old["conclusion"] != "failure"
            or old["run_attempt"] != 1):
        raise ValueError("Pinned producer run identity/status mismatch")
    jobs = api(f"repos/{repo}/actions/runs/{RECOVERY_RUN}/attempts/1/jobs?per_page=100")["jobs"]
    succeeded = {j["id"]: j["name"] for j in jobs if j["conclusion"] == "success"}
    if any(succeeded.get(jid) != name for jid, name in RECOVERY_JOBS.items()):
        raise ValueError("Pinned producer build/package jobs did not all succeed")
    return RECOVERY_RUN, RECOVERY_SHA, pinned_items(artifacts(repo, RECOVERY_RUN)), True


def verify_manifest(target, group, commit, recovery):
    manifest = json.loads((target/(group+"-native-manifest.json")).read_text(encoding="utf-8"))
    exp = manifest["experiment"]
    if (manifest["source"]["commit"] != UPSTREAM or exp["workflow_commit"] != commit
            or exp["localization_commit"] != OVERLAY or set(exp["groups"]) != set(GROUPS)
            or exp["groups"]["A0"]["sha256"] != exp["a0_verified_sha256"]):
        raise ValueError("Native manifest identity mismatch")
    if any(not HEX.fullmatch(exp["groups"][g]["sha256"]) for g in GROUPS):
        raise ValueError("Invalid native hash")
    if recovery and exp["trace_enabled"] is not True:
        raise ValueError("Recovery requires the traced build")
    if exp["trace_enabled"] and (len(exp["trace_reports"]) != 3
            or any(not HEX.fullmatch(v) for v in exp["trace_reports"].values())):
        raise ValueError("Missing trace digests")
    line = (target/(group+".sha256")).read_text(encoding="utf-8").strip()
    if line != sha(target/(group+".exe"))+"  "+group+".exe":
        raise ValueError("EXE checksum mismatch")
    return manifest


def prepare_smoke(repo, run, commit):
    group = os.environ["GROUP"]
    names = package_names(group)
    producer_run, producer_sha, items, recovery = producer(repo, run, commit)
    pkg = exact(items, "experiment-exe-"+group)
    target = Path("package-input")
    target.mkdir(exist_ok=False)
    download(repo, producer_run, pkg, target, names)
    verify_manifest(target, group, producer_sha, recovery)
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as out:
        out.write(f"id={pkg['id']}\ndigest={pkg['digest']}\nproducer_run={producer_run}\nproducer_sha={producer_sha}\n")
    print(f"Verified exact package {group}: artifact {pkg['id']} from producer run {producer_run}")


def main():
    repo,run,commit = (os.environ[k] for k in ("GITHUB_REPOSITORY","GITHUB_RUN_ID","GITHUB_SHA"))
    if sys.argv[1:] == ["--prepare-smoke"]:
        prepare_smoke(repo, run, commit)
        return
    if sys.argv[1:]:
        raise ValueError("Unknown arguments")
    producer_run, producer_sha, packages, recovery = producer(repo, run, commit)
    items = artifacts(repo, run)
    dist = Path("dist")
    dist.mkdir()
    receipts = []
    with tempfile.TemporaryDirectory() as tmp:
        for g in GROUPS:
            receipt_dir = Path(tmp)/g
            download(repo,run,exact(items,"smoke-receipt-"+g),receipt_dir,["smoke-receipt.json"])
            receipt = json.loads((receipt_dir/"smoke-receipt.json").read_text(encoding="utf-8-sig"))
            pkg = exact(packages,"experiment-exe-"+g)
            names = package_names(g)
            if (receipt["group"] != g or receipt["runId"] != run or receipt["sourceSha"] != commit
                    or receipt["producerRunId"] != producer_run or receipt["producerSha"] != producer_sha
                    or receipt["artifactId"] != str(pkg["id"]) or receipt["artifactDigest"] != pkg["digest"]
                    or set(receipt["files"]) != set(names)):
                raise ValueError("Smoke receipt identity mismatch: "+g)
            download(repo,producer_run,pkg,dist,names)
            if any(sha(dist/n) != receipt["files"][n] for n in names):
                raise ValueError("Smoke receipt file hash mismatch: "+g)
            manifest = verify_manifest(dist, g, producer_sha, recovery)
            if receipt["loadedAddonSha256"] != manifest["experiment"]["groups"][g]["sha256"]:
                raise ValueError("Loaded addon receipt mismatch: "+g)
            receipts.append(receipt)
    (dist/"SHA256SUMS.txt").write_text("".join(sha(dist/(g+".exe"))+"  "+g+".exe\n" for g in GROUPS))
    (dist/"smoke-receipts.json").write_text(json.dumps(receipts,indent=2)+"\n")
    (dist/"producer.json").write_text(json.dumps({"producerRunId":producer_run,"producerSha":producer_sha,
        "validationRunId":run,"validationSha":commit,"recovered":recovery},indent=2)+"\n")
    print("Collected exact smoke-accepted artifact IDs and hashes.")

if __name__ == "__main__":
    main()
