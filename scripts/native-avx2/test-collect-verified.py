import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("collect", Path(__file__).with_name("collect-verified.py"))
collect = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collect)

class RecoveryTests(unittest.TestCase):
    def items(self):
        return [{"id": aid, "name": name, "digest": "sha256:"+digest, "expired":False}
                for name, (aid,digest) in collect.RECOVERY_PINS.items()]

    def test_pinned_set(self):
        self.assertEqual(len(collect.pinned_items(self.items())),4)

    def test_wrong_id(self):
        items=self.items()
        items[0]["id"]+=1
        with self.assertRaises(ValueError):
            collect.pinned_items(items)

    def test_wrong_digest(self):
        items=self.items()
        items[0]["digest"]="sha256:"+"0"*64
        with self.assertRaises(ValueError):
            collect.pinned_items(items)

    def test_duplicate_and_expired(self):
        items=self.items()
        with self.assertRaises(ValueError):
            collect.pinned_items(items+[dict(items[0])])
        items[0]["expired"]=True
        with self.assertRaises(ValueError):
            collect.pinned_items(items)

    def test_recovery_run_and_jobs(self):
        old={"id":int(collect.RECOVERY_RUN),"head_sha":collect.RECOVERY_SHA,"head_branch":"experiment/native-avx2",
             "path":".github/workflows/native-avx2-experiment.yml","status":"completed","conclusion":"failure","run_attempt":1}
        jobs={"jobs":[{"id":jid,"name":name,"conclusion":"success"} for jid,name in collect.RECOVERY_JOBS.items()]}
        with patch.dict(os.environ,{"RECOVER_SMOKE":"true"}),patch.object(collect,"api",side_effect=[old,jobs]),patch.object(collect,"artifacts",return_value=self.items()):
            run,sha,items,recovered=collect.producer("WhiCCX0/oh-my-pi","10","new")
            self.assertEqual((run,sha,recovered),(collect.RECOVERY_RUN,collect.RECOVERY_SHA,True))
        jobs["jobs"][0]["conclusion"]="failure"
        with patch.dict(os.environ,{"RECOVER_SMOKE":"true"}),patch.object(collect,"api",side_effect=[old,jobs]):
            with self.assertRaises(ValueError):
                collect.producer("WhiCCX0/oh-my-pi","10","new")

    def test_normal_same_run(self):
        with patch.dict(os.environ,{"RECOVER_SMOKE":"false"}),patch.object(collect,"artifacts",return_value=[]) as api:
            self.assertEqual(collect.producer("repo","10","commit"),("10","commit",[],False))
            api.assert_called_once_with("repo","10")

if __name__ == "__main__":
    unittest.main()
