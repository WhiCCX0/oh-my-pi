"""Regression checks for extended gates and fixed phase-1 matrix."""
import importlib.util
import unittest
from pathlib import Path

def load(n):
    spec=importlib.util.spec_from_file_location(n,Path(__file__).with_name(n+".py"))
    m=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m
p=load("phase1-overlay")
f=load("test-aquery")
class Tests(unittest.TestCase):
    def test_matrix(self):
        self.assertEqual(set(p.GROUPS),{"D16","D1","E16","E1","H16","H1"})
        for g,c in p.GROUPS.items():
            self.assertEqual(c["reference"],"B" if c["cgu"]==16 else "C")
            self.assertEqual(c["local_only"],g.startswith("D"))
    def test_cpu_lto(self):
        for cpu,lto in (("raptorlake","thin"),("x86-64-v3","fat")):
            graph=f.fixture()
            for a in graph["actions"]:
                for pf in a.get("paramFiles",[]):
                    pf["arguments"]=[x.replace("target-cpu=x86-64-v3","target-cpu="+cpu).replace("lto=thin","lto="+lto) for x in pf["arguments"]]
            self.assertEqual(f.gate.inspect(graph,"modern",16,modern_cpu=cpu,lto=lto)[0],[])
            self.assertTrue(f.gate.inspect(graph,"modern",16)[0])
    def test_raptorlake_trace(self):
        t=load("test-compiler-trace")
        fixture=t.TraceTests()
        try:
            trace=fixture.wrapper_fixture(["/clang:-march=raptorlake"])
            self.assertEqual(t.gate.inspect(trace,"modern","raptorlake")["errors"],[])
            self.assertTrue(t.gate.inspect(trace,"modern")["errors"])
        finally:
            fixture.doCleanups()
    def test_anchor_failure(self):
        with self.assertRaises(ValueError):
            p.replace("a a","a","b")
if __name__=="__main__":
    unittest.main()
