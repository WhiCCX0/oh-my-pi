"""Collect only artifacts whose immutable ID and bytes were accepted by Windows smoke."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
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
    return json.loads(subprocess.check_output(["gh", "api", path], text=True))

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

def main():
    repo,run,commit = (os.environ[k] for k in ("GITHUB_REPOSITORY","GITHUB_RUN_ID","GITHUB_SHA"))
    pages = json.loads(subprocess.check_output(["gh","api","--paginate","--slurp",
                       f"repos/{repo}/actions/runs/{run}/artifacts?per_page=100"],text=True))
    items = [a for p in pages for a in p["artifacts"]]
    dist = Path("dist")
    dist.mkdir()
    receipts = []
    with tempfile.TemporaryDirectory() as tmp:
        for g in GROUPS:
            receipt_dir = Path(tmp)/g
            download(repo,run,exact(items,"smoke-receipt-"+g),receipt_dir,["smoke-receipt.json"])
            receipt = json.loads((receipt_dir/"smoke-receipt.json").read_text(encoding="utf-8-sig"))
            pkg = exact(items,"experiment-exe-"+g)
            names = [g+".exe",g+"-native-manifest.json",g+"-LICENSE",g+"-LICENSE-dictionary",g+".sha256"]
            if (receipt["group"] != g or receipt["runId"] != run or receipt["sourceSha"] != commit
                    or receipt["artifactId"] != str(pkg["id"]) or receipt["artifactDigest"] != pkg["digest"]
                    or set(receipt["files"]) != set(names)):
                raise ValueError("Smoke receipt identity mismatch: "+g)
            download(repo,run,pkg,dist,names)
            if any(sha(dist/n) != receipt["files"][n] for n in names):
                raise ValueError("Smoke receipt file hash mismatch: "+g)
            manifest = json.loads((dist/(g+"-native-manifest.json")).read_text())
            if (manifest["experiment"]["workflow_commit"] != commit
                    or receipt["loadedAddonSha256"] != manifest["experiment"]["groups"][g]["sha256"]):
                raise ValueError("Loaded addon receipt mismatch: "+g)
            receipts.append(receipt)
    (dist/"SHA256SUMS.txt").write_text("".join(sha(dist/(g+".exe"))+"  "+g+".exe\n" for g in GROUPS))
    (dist/"smoke-receipts.json").write_text(json.dumps(receipts,indent=2)+"\n")
    print("Collected exact smoke-accepted artifact IDs and hashes.")

if __name__ == "__main__":
    main()
