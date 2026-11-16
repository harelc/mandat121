"""Era guard: "which Knesset" is data, never a silent default.

The K26 launch kept surfacing the same bug shape — shared code that
quietly assumed K25: builders defaulting --knesset to 25, a saved-vote
migration resolving ids against the K25 index only (it wiped K26
votes), openBillPage() no-oping on K26 bills. Each instance was
invisible until a human noticed missing data. These tests pin the
systemic fixes so the next era (or the next shared helper) can't
reintroduce the pattern unnoticed:

  * every era-parameterized builder requires --knesset explicitly;
  * every workflow invocation of one names its era;
  * the R2 shard uploader knows every corpus the client can request;
  * the frontend's shared era-crossing helpers resolve against both
    eras' indexes.
"""
import importlib.util
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent

ERA_BUILDERS = [
    "build_knesset.py", "build_committees.py", "build_protocols.py",
    "build_vote_results.py", "build_agenda.py", "build_plenum_search.py",
    "build_committee_search.py", "build_k25_tenure.py",
    "build_k25_committee_members.py", "build_k25_roles.py",
    "build_k25_list_order.py", "build_k25_bill_history.py",
    "build_mk_contacts.py",
]


def load_module(name):
    spec = importlib.util.spec_from_file_location(name[:-3], ROOT / name)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class KnessetArgRequiredTest(unittest.TestCase):
    def test_no_builder_defaults_its_era(self):
        for name in ERA_BUILDERS:
            src = (ROOT / name).read_text("utf-8")
            with self.subTest(builder=name):
                self.assertNotRegex(
                    src, r'add_argument\("--knesset"[^)]*default=',
                    f"{name} defaults --knesset — the era must be named")
                self.assertTrue(
                    'add_argument("--knesset", type=knesset_type, required=True' in src
                    or "the era is required" in src,
                    f"{name} must require --knesset explicitly (era_registry.knesset_type)")

    def test_workflows_name_the_era(self):
        pat = re.compile(r"python3?\s+(?:-u\s+)?(" + "|".join(ERA_BUILDERS) + r")\b")
        for wf in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
            for i, line in enumerate(wf.read_text("utf-8").splitlines(), 1):
                m = pat.search(line)
                if m:
                    with self.subTest(workflow=wf.name, line=i):
                        self.assertIn("--knesset", line,
                                      f"{wf.name}:{i} runs {m.group(1)} without naming an era")


class BuilderImportsTest(unittest.TestCase):
    def test_knesset_type_defined_at_module_level(self):
        # Regression (run 43814011849): a scripted edit inserted the
        # era_registry import inside build_agenda.py's docstring, so
        # --knesset parsing died with NameError in the daily workflow.
        # Every era builder must actually HAVE the name after import.
        for name in ERA_BUILDERS:
            with self.subTest(builder=name):
                mod = load_module(name)
                self.assertTrue(hasattr(mod, "knesset_type"),
                                f"{name}: knesset_type missing at module level "
                                "(import inside docstring/string?)")


class ShardUploaderCorporaTest(unittest.TestCase):
    def test_uploader_knows_every_client_corpus(self):
        mod = load_module("upload_search_shards.py")
        self.assertEqual(set(mod.CORPORA), {"c", "p", "c26", "p26"})
        for corpus, path in mod.CORPORA.items():
            self.assertEqual(path, ROOT / "data" / f"search_{corpus}")


def _fn_body(html, name):
    i = html.index(f"function {name}(")
    j = html.index("\nfunction ", i + 1)
    return html[i:j]


class FrontendEraHelpersTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "index.html").read_text("utf-8")

    def test_my_votes_migration_resolves_both_eras(self):
        self.assertIn("K26V_BY_ID", _fn_body(self.html, "migrateMyVotes"))

    def test_open_bill_page_opens_both_eras(self):
        body = _fn_body(self.html, "openBillPage")
        self.assertIn("openBill26", body)
        self.assertIn("openBillAll", body)

    def test_plenum_for_date_defaults_to_current_era(self):
        self.assertIn("function plenumForDate(date,kn=KN)", self.html)


class EraLiteralFreezeTest(unittest.TestCase):
    """No NEW era-dependent code paths. The count of KN/kn === <number>
    comparisons in index.html is frozen at 2 — both inside the era
    accessor layer itself (eraCorpus, voteModelAny), the one place era
    numbers are data. Everything else routes through ERAS /
    ERA_DATASETS / eraClosed / CURRENT_KN / ERA_PAGE_VARIANTS. Adding
    an era branch anywhere else is how the K26 divergences happened;
    if one is truly justified, bump the baseline IN THE SAME COMMIT
    with the justification in ROADMAP — that bump is the reviewable
    act, never a silent edit."""

    BASELINE = 2

    def test_era_literal_count_frozen(self):
        html = (ROOT / "index.html").read_text("utf-8")
        n = len(re.findall(r"\b(?:KN|kn)\s*[!=]={1,3}\s*\d{2}\b", html))
        self.assertLessEqual(
            n, self.BASELINE,
            f"{n} era-literal comparisons in index.html > frozen baseline "
            f"{self.BASELINE}: route through ERAS/eraBills/eraVotes/billAny "
            "or justify + bump the baseline (see ROADMAP era contract)")

    def test_registry_has_exactly_one_current_era(self):
        html = (ROOT / "index.html").read_text("utf-8")
        m = re.search(r"const ERAS=\[(.*?)\];", html, re.S)
        self.assertIsNotNone(m, "index.html must define the ERAS registry")
        self.assertEqual(m.group(1).count("current:true"), 1)
        kns = re.findall(r"\{kn:(\d+),", m.group(1))
        self.assertEqual(len(kns), len(set(kns)), "era numbers unique in ERAS")

    def test_no_builder_restricts_to_two_eras(self):
        for p in sorted(ROOT.glob("build_*.py")) + [ROOT / "probe_knesset.py"]:
            with self.subTest(builder=p.name):
                self.assertNotIn("choices=(25, 26)", p.read_text("utf-8"),
                                 f"{p.name} hardcodes the era set — any Knesset number must work")


class AuditFollowupTest(unittest.TestCase):
    """Pins for the Nov-2026 K25-only audit (three sweeps over index.html
    + builders): each of these was a silent K25 default reachable from
    K26 pages, invisible until a human noticed wrong output."""

    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "index.html").read_text("utf-8")

    def test_my_agreement_uses_current_era_index(self):
        self.assertIn("IDX26", _fn_body(self.html, "myAgreement"))

    def test_proto_color_uses_current_era_faction(self):
        self.assertIn("K26_FACTIONS", _fn_body(self.html, "protoColor"))

    def test_roles_cache_is_era_keyed(self):
        self.assertIn("ROLES_KN", self.html)

    def test_mine_ranking_heading_not_hardcoded_25(self):
        self.assertNotIn('מי מצביע כמוני <span class="chip acc">כנסת 25</span>', self.html)

    def test_open_mk_resolves_era(self):
        self.assertIn("mkKn(name)", _fn_body(self.html, "openMk"))

    def test_bill_link_maps_are_per_era(self):
        body = _fn_body(self.html, "billLinkMaps")
        self.assertIn("per-era", body)
        self.assertNotIn("for(const b of [...ALLB,...K26B])", body)

    def test_list_order_refuses_unknown_era(self):
        src = (ROOT / "build_k25_list_order.py").read_text("utf-8")
        self.assertIn("refusing to overwrite data/k25_list_order.json", src)

    def test_committees_recent_since_never_keyerrors(self):
        src = (ROOT / "build_committees.py").read_text("utf-8")
        self.assertIn("def recent_since_for", src)
        self.assertNotIn("RECENT_SINCE[kn]", src)

    def test_events_discovers_eras_from_disk(self):
        src = (ROOT / "build_events.py").read_text("utf-8")
        self.assertNotIn("for kn in (25, 26)", src)
        self.assertIn("sorted(eras)", src)

    def test_protocols_search_excludes_every_non25_era(self):
        src = (ROOT / "build_protocols_search.py").read_text("utf-8")
        self.assertIn('glob("k*_protocols_index.json")', src)


if __name__ == "__main__":
    unittest.main()
