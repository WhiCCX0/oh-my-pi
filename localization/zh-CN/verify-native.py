"""Verify the signed registry statement before extracting upstream Windows native files."""
import base64
import hashlib
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile


def verify_and_extract(archive, audit, version, commit, target):
    name = "@oh-my-pi/pi-natives-win32-x64"
    digest = hashlib.sha512()
    with archive.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    report = json.loads(audit.read_text())
    if report["invalid"] or report["missing"]:
        raise ValueError("npm signature audit failed")
    verified = [v for v in report["verified"] if v["name"] == name and v["version"] == version
                and v["registry"].rstrip("/") == "https://registry.npmjs.org"]
    if len(verified) != 1:
        raise ValueError("Missing unique verified npm provenance")
    trusted = False
    for item in verified[0]["attestationBundles"]:
        bundle = item["bundle"]
        statement = json.loads(base64.b64decode(bundle["dsseEnvelope"]["payload"], validate=True))
        predicate_type = statement.get("predicateType")
        if predicate_type not in ("https://slsa.dev/provenance/v1", "https://slsa.dev/provenance/v0.2"):
            continue
        if not any(s.get("digest", {}).get("sha512") == digest.hexdigest()
                   for s in statement["subject"]):
            continue
        predicate = statement["predicate"]
        dependencies = predicate.get("buildDefinition", {}).get("resolvedDependencies", []) + predicate.get("materials", [])
        source_ok = any(re.fullmatch(r"(?:git\+)?https://github\.com/can1357/oh-my-pi(?:\.git)?(?:@refs/[^ ]+)?", dep.get("uri", ""))
                        and (dep.get("digest", {}).get("gitCommit") == commit or dep.get("digest", {}).get("sha1") == commit)
                        for dep in dependencies)
        material = bundle["verificationMaterial"]
        cert = material.get("certificate") or material.get("x509CertificateChain", {}).get("certificates", [{}])[0]
        if not cert.get("rawBytes"):
            continue
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "cert.der"
            path.write_bytes(base64.b64decode(cert["rawBytes"], validate=True))
            san = subprocess.check_output(["openssl", "x509", "-inform", "DER", "-in", str(path),
                                           "-noout", "-ext", "subjectAltName"], text=True)
        identity_ok = bool(re.search(r"URI:https://github\.com/can1357/oh-my-pi/\.github/workflows/ci\.yml@refs/(?:heads/main|tags/v[0-9]+\.[0-9]+\.[0-9]+)(?=\s|,|$)", san))
        if source_ok and identity_ok:
            trusted = True
            break
    if not trusted:
        raise ValueError("No verified provenance binds this tarball to upstream ci.yml and exact source commit")
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        members = {}
        for member in tar.getmembers():
            p = pathlib.PurePosixPath(member.name)
            if p.is_absolute() or "\\" in member.name or ".." in p.parts or not p.parts or p.parts[0] != "package":
                raise ValueError("Unsafe tar path: " + member.name)
            if not (member.isfile() or member.isdir()) or member.name in members:
                raise ValueError("Unsafe or duplicate tar member: " + member.name)
            members[member.name] = member
        def read(path):
            member = members[path]
            if not member.isfile():
                raise ValueError("Expected regular file: " + path)
            return tar.extractfile(member)
        with read("package/package.json") as file:
            meta = json.load(file)
        if meta["name"] != name or meta["version"] != version:
            raise ValueError("Native package identity mismatch")
        files = [p for p in members if re.fullmatch(r"package/pi_natives\.win32-x64(?:-baseline|-modern)?\.node", p)]
        if not files:
            raise ValueError("Missing Windows addon")
        files.append("package/THIRD-PARTY-NOTICES.txt")
        for path in files:
            if members[path].size <= 0:
                raise ValueError("Empty required file: " + path)
            with read(path) as source, (target / pathlib.PurePosixPath(path).name).open("wb") as dest:
                shutil.copyfileobj(source, dest)
    print("Verified upstream npm signature, provenance, archive and selected native files.")


if __name__ == "__main__":
    archive, audit, version, commit, target = sys.argv[1:]
    verify_and_extract(pathlib.Path(archive), pathlib.Path(audit), version, commit, pathlib.Path(target))
