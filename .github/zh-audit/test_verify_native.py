"""Offline policy/extraction regression tests; npm/Sigstore crypto is exercised by release CI."""
import base64
import hashlib
import importlib.util
import io
import json
import pathlib
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("verify_native", ROOT / "localization/zh-CN/verify-native.py")
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)

NAME = "@oh-my-pi/pi-natives-win32-x64"
VERSION = "19.2.1"
COMMIT = "a" * 40
SAN = "URI:https://github.com/can1357/oh-my-pi/.github/workflows/ci.yml@refs/heads/main"
ISSUER = "\n            1.3.6.1.4.1.57264.1.8: \n                ..https://token.actions.githubusercontent.com\n"
ADDON = "package/pi_natives.win32-x64-baseline.node"

class NativePolicyTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(tmp.name)
        self.archive = self.root / "native.tgz"
        self.audit = self.root / "audit.json"
        self.target = self.root / "verified"
        self.files = [
            ("package/package.json", json.dumps({"name": NAME, "version": VERSION}).encode()),
            (ADDON, b"native-fixture"),
            ("package/THIRD-PARTY-NOTICES.txt", b"license"),
            ("package/unexpected.js", b"not extracted"),
        ]
        self.write_archive()
        self.statement = {
            "predicateType": "https://slsa.dev/provenance/v1",
            "subject": [{"digest": {"sha512": hashlib.sha512(self.archive.read_bytes()).hexdigest()}}],
            "predicate": {"buildDefinition": {"resolvedDependencies": [
                {"uri": "git+https://github.com/can1357/oh-my-pi@refs/heads/main",
                 "digest": {"gitCommit": COMMIT}}
            ]}},
        }
        self.verified = {"name": NAME, "version": VERSION, "registry": "https://registry.npmjs.org/",
                         "attestationBundles": [{"bundle": {"dsseEnvelope": {},
                         "verificationMaterial": {"certificate": {"rawBytes": "YQ=="}}}}]}
        self.report = {"verified": [self.verified], "invalid": [], "missing": []}

    def write_archive(self, extra=()):
        with tarfile.open(self.archive, "w:gz") as tar:
            for name, data in self.files:
                member = tarfile.TarInfo(name)
                member.size = len(data)
                tar.addfile(member, io.BytesIO(data))
            for member in extra:
                tar.addfile(member)

    def refresh_digest(self):
        self.statement["subject"][0]["digest"]["sha512"] = hashlib.sha512(self.archive.read_bytes()).hexdigest()

    def verify(self, san=SAN, issuer=ISSUER):
        self.verified["attestationBundles"][0]["bundle"]["dsseEnvelope"]["payload"] = (
            base64.b64encode(json.dumps(self.statement).encode()).decode()
        )
        self.audit.write_text(json.dumps(self.report))
        # Only certificate rendering is stubbed; this tests policy, not cryptographic signature validation.
        with patch.object(native.subprocess, "check_output", return_value="X509v3 Subject Alternative Name: \n    " + san + issuer):
            native.verify_and_extract(self.archive, self.audit, VERSION, COMMIT, self.target)

    def reject(self, message, san=SAN, issuer=ISSUER):
        with self.assertRaisesRegex(ValueError, message):
            self.verify(san, issuer)

    def test_valid_extracts_only_required_files(self):
        self.verify()
        self.assertEqual({p.name for p in self.target.iterdir()},
                         {"pi_natives.win32-x64-baseline.node", "THIRD-PARTY-NOTICES.txt"})
        self.assertEqual((self.target / pathlib.PurePosixPath(ADDON).name).read_bytes(), b"native-fixture")

    def test_invalid_or_missing_signature(self):
        for key in ("invalid", "missing"):
            with self.subTest(key=key):
                self.report[key] = ["fixture"]
                self.reject("signature audit failed")
                self.report[key] = []

    def test_registry_identity(self):
        self.verified["registry"] = "https://example.invalid"
        self.reject("Missing unique verified npm provenance")

    def test_duplicate_verified_package(self):
        self.report["verified"].append(self.verified)
        self.reject("Missing unique verified npm provenance")

    def test_wrong_tarball_digest(self):
        self.statement["subject"][0]["digest"]["sha512"] = "0" * 128
        self.reject("No verified provenance")

    def test_wrong_commit(self):
        self.statement["predicate"]["buildDefinition"]["resolvedDependencies"][0]["digest"]["gitCommit"] = "b" * 40
        self.reject("No verified provenance")

    def test_wrong_repository(self):
        self.statement["predicate"]["buildDefinition"]["resolvedDependencies"][0]["uri"] = "https://github.com/can1357/oh-my-pi-other"
        self.reject("No verified provenance")

    def test_wrong_workflow_identity(self):
        self.reject("No verified provenance", SAN.replace("/ci.yml", "/other.yml"))

    def test_wrong_workflow_ref(self):
        self.reject("No verified provenance", SAN.replace("/heads/main", "/heads/main-other"))

    def test_unsupported_predicate(self):
        self.statement["predicateType"] = "https://example.invalid"
        self.reject("No verified provenance")

    def test_path_traversal(self):
        self.files.append(("package/../../escape", b"bad"))
        self.write_archive()
        self.refresh_digest()
        self.reject("Unsafe tar path")
        self.assertFalse((self.root / "escape").exists())

    def test_symlink(self):
        member = tarfile.TarInfo("package/link")
        member.type = tarfile.SYMTYPE
        member.linkname = "/etc/passwd"
        self.write_archive([member])
        self.refresh_digest()
        self.reject("Unsafe or duplicate tar member")

    def test_duplicate_member(self):
        self.files.append((ADDON, b"duplicate"))
        self.write_archive()
        self.refresh_digest()
        self.reject("Unsafe or duplicate tar member")

    def test_wrong_package_version(self):
        self.files[0] = ("package/package.json", json.dumps({"name": NAME, "version": "0.0.0"}).encode())
        self.write_archive()
        self.refresh_digest()
        self.reject("Native package identity mismatch")

    def test_missing_addon(self):
        self.files = [item for item in self.files if item[0] != ADDON]
        self.write_archive()
        self.refresh_digest()
        self.reject("Missing Windows addon")

    def test_wrong_issuer(self):
        self.reject("No verified provenance", issuer=ISSUER.replace("token.actions.githubusercontent.com", "example.invalid"))
        self.reject("No verified provenance", issuer="")

    def test_san_prefix_and_exact_tag(self):
        self.reject("No verified provenance", "X" + SAN)
        self.reject("No verified provenance", SAN.replace("heads/main", "tags/v0.0.0"))

    def test_empty_cert_chain(self):
        self.verified["attestationBundles"][0]["bundle"]["verificationMaterial"] = {"x509CertificateChain": {"certificates": []}}
        self.reject("No verified provenance")

    def test_normalized_duplicate(self):
        self.files.append(("package/./" + pathlib.PurePosixPath(ADDON).name, b"duplicate"))
        self.write_archive()
        self.refresh_digest()
        self.reject("Unsafe or duplicate tar member")

    def test_missing_notices(self):
        self.files = [item for item in self.files if not item[0].endswith("NOTICES.txt")]
        self.write_archive()
        self.refresh_digest()
        self.reject("Missing required file")

    def test_special_tar_members(self):
        for kind in (tarfile.LNKTYPE, tarfile.FIFOTYPE):
            with self.subTest(kind=kind):
                member = tarfile.TarInfo("package/special")
                member.type = kind
                member.linkname = ADDON
                self.write_archive([member])
                self.refresh_digest()
                self.reject("Unsafe or duplicate tar member")

    def test_empty_addon(self):
        self.files[1] = (ADDON, b"")
        self.write_archive()
        self.refresh_digest()
        self.reject("Empty required file")

if __name__ == "__main__":
    unittest.main()
