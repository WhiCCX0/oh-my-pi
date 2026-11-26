"""Pure release planning; no network or side effects."""
import json
import re
import sys
import unittest


def plan(pages, version, minimum, sha, scheduled):
    prefix = "zh-" + version + "-r"
    revisions = {}
    for page in pages:
        for release in page:
            match = re.fullmatch(re.escape(prefix) + r"([1-9][0-9]*)", release["tag_name"])
            if not match:
                continue
            n = int(match.group(1))
            if n in revisions:
                raise ValueError("Duplicate release tag: " + release["tag_name"])
            revisions[n] = release
    eligible = {n: v for n, v in revisions.items() if n >= minimum}
    published = any(not v["draft"] for v in revisions.values())
    published_eligible = any(not v["draft"] for v in eligible.values())
    matching = [n for n, v in eligible.items() if v["draft"] and v["target_commitish"] == sha]
    if scheduled and published_eligible:
        return {"build": False, "sync": False, "tag": ""}
    if matching:
        n = max(matching)
    else:
        # A stale draft never causes a second scheduled public release:
        # any published eligible revision above will stop later schedules.
        n = max([minimum - 1, *revisions]) + 1
    return {"build": True, "sync": not published, "tag": prefix + str(n)}


class PlanTests(unittest.TestCase):
    def test_scheduled_stale_draft(self):
        stale = {"tag_name": "zh-v1.2.3-r1", "draft": True, "target_commitish": "old"}
        args = ("v1.2.3", 1, "new", True)
        self.assertEqual(plan([[stale]], *args)["tag"], "zh-v1.2.3-r2")
        published = dict(stale, tag_name="zh-v1.2.3-r2", draft=False)
        self.assertFalse(plan([[stale, published]], *args)["build"])
        self.assertFalse(plan([[stale, published]], *args)["sync"])

    def test_resume_and_manual(self):
        draft = {"tag_name": "zh-v1.2.3-r3", "draft": True, "target_commitish": "new"}
        public = dict(draft, tag_name="zh-v1.2.3-r1", draft=False)
        self.assertEqual(plan([[draft]], "v1.2.3", 1, "new", True)["tag"], draft["tag_name"])
        self.assertFalse(plan([[draft, public]], "v1.2.3", 1, "new", True)["build"])
        self.assertEqual(plan([[draft, public]], "v1.2.3", 1, "new", False)["tag"], draft["tag_name"])
        self.assertEqual(plan([[public]], "v1.2.3", 1, "new", False)["tag"], "zh-v1.2.3-r2")

    def test_empty_bump_duplicates(self):
        self.assertTrue(plan([[]], "v1.2.3", 1, "new", True)["sync"])
        public = {"tag_name": "zh-v1.2.3-r1", "draft": False, "target_commitish": "old"}
        self.assertEqual(plan([[public]], "v1.2.3", 2, "new", True)["tag"], "zh-v1.2.3-r2")
        with self.assertRaises(ValueError):
            plan([[public], [public]], "v1.2.3", 1, "new", True)


if __name__ == "__main__":
    if sys.argv[1:] == ["--test"]:
        unittest.main(argv=[sys.argv[0]])
    else:
        version, minimum, sha, event = sys.argv[1:]
        print(json.dumps(plan(json.load(sys.stdin), version, int(minimum), sha, event == "schedule")))
