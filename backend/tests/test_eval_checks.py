"""The eval scorers are the measuring instrument, so they get tested like
any other code: a scorer that silently reports 1.0 is worse than no eval."""

import unittest

from evals import checks


class VerbatimOverlapTests(unittest.TestCase):
    AUTHOR = "Honestly I have no idea why this lib used netloc and manual parsing instead of hostname as I can see references"

    def test_copying_the_author_scores_high(self):
        copied = "Honestly I have no idea why this lib used netloc and manual parsing instead of hostname."
        self.assertGreater(checks.verbatim_overlap(copied, self.AUTHOR), 0.8)

    def test_restating_the_reason_scores_low(self):
        restated = "Replaces manual netloc parsing with the hostname property to stop credentials leaking, per CVE-2024-47081."
        self.assertLess(checks.verbatim_overlap(restated, self.AUTHOR), 0.2)

    def test_empty_generated_text_is_zero_not_an_error(self):
        self.assertEqual(checks.verbatim_overlap("", self.AUTHOR), 0.0)


class SummaryScoringTests(unittest.TestCase):
    INPUTS = [{"path": "a.py", "tier": "source"}, {"path": "b.md", "tier": "brief"}, {"path": "c.py", "tier": "source"}]

    def test_missing_and_unavailable_summaries_count_against_coverage(self):
        outputs = {"a.py": "x" * 400, "b.md": "Summary unavailable (could not be parsed)."}
        self.assertAlmostEqual(checks.score_summaries(self.INPUTS, outputs)["coverage"], 1 / 3)

    def test_length_bounds_are_per_tier(self):
        outputs = {"a.py": "`Session` " + "x" * 400, "b.md": "y" * 100, "c.py": "too short"}
        metrics = checks.score_summaries(self.INPUTS, outputs)
        self.assertEqual(metrics["coverage"], 1.0)
        self.assertAlmostEqual(metrics["length_in_bounds"], 2 / 3)

    def test_boilerplate_openings_are_detected(self):
        outputs = {"a.py": "This file contains helpers. " + "x" * 300, "b.md": "Explains the release process to maintainers.", "c.py": "`Pool` owns connections. " + "x" * 300}
        self.assertAlmostEqual(checks.score_summaries(self.INPUTS, outputs)["boilerplate_opening"], 1 / 3)


class PrScoringTests(unittest.TestCase):
    INPUTS = [{"number": 1, "stated": "Fix the credential leak by using hostname."}, {"number": 2, "stated": None}]

    def test_clean_output_passes_the_gate(self):
        outputs = [
            {"number": 1, "rationale_stated": "Uses `hostname` so credentials cannot leak.", "rationale_inferred": None, "confidence": "high"},
            {"number": 2, "rationale_stated": None, "rationale_inferred": "Likely a refactor.", "confidence": "low"},
        ]
        metrics = checks.score_prs(self.INPUTS, outputs)
        self.assertEqual(metrics["kind_matches_input"], 1.0)
        self.assertEqual(metrics["confidence_consistent"], 1.0)
        self.assertEqual(checks.gate("prs", metrics), [])

    def test_setting_both_rationales_breaks_the_integrity_rule(self):
        outputs = [{"number": 1, "rationale_stated": "a", "rationale_inferred": "b", "confidence": "high"}, {"number": 2, "rationale_inferred": "c", "confidence": "low"}]
        metrics = checks.score_prs(self.INPUTS, outputs)
        self.assertEqual(metrics["stated_and_inferred_both_set"], 1)
        self.assertTrue(any("both_set" in f for f in checks.gate("prs", metrics)))

    def test_a_confident_guess_is_inconsistent(self):
        outputs = [{"number": 2, "rationale_stated": None, "rationale_inferred": "guess", "confidence": "high"}]
        self.assertEqual(checks.score_prs(self.INPUTS[1:], outputs)["confidence_consistent"], 0.0)

    def test_inventing_a_stated_reason_is_caught_without_labels(self):
        outputs = [{"number": 2, "rationale_stated": "The author wanted speed.", "rationale_inferred": None, "confidence": "high"}]
        self.assertEqual(checks.score_prs(self.INPUTS[1:], outputs)["kind_matches_input"], 0.0)

    def test_hand_labels_are_used_when_present(self):
        outputs = [{"number": 1, "rationale_stated": "x", "confidence": "high"}, {"number": 2, "rationale_inferred": "y", "confidence": "low"}]
        metrics = checks.score_prs(self.INPUTS, outputs, {"pr_kind": {"1": "stated", "2": "stated", "3": None}})
        self.assertEqual(metrics["labelled_prs"], 2)
        self.assertEqual(metrics["kind_accuracy_vs_labels"], 0.5)


class MapScoringTests(unittest.TestCase):
    def test_unparseable_response_is_unusable(self):
        self.assertEqual(checks.score_map(None, None, ["bad json"]), {"usable": 0.0})

    def test_hallucinated_paths_and_empty_labels_are_measured(self):
        raw = {"members": [{"path": "a.py"}, {"path": "b.py"}, {"path": "made/up.py"}, {"path": "nope.py"}],
               "edges": [{"from": "a.py", "to": "b.py", "label": "imports"}, {"from": "a.py", "to": "b.py", "label": "prepared request"}]}
        validated = {"groups": [{"id": "g"}], "nodes": [{"id": "a.py"}, {"id": "b.py"}, {"id": "ext:x", "external": True}],
                     "edges": [{"source": "a.py", "target": "b.py", "backed": True}]}
        metrics = checks.score_map(raw, validated, ["p1"], {"key_files": ["a.py", "z.py"]})
        self.assertEqual(metrics["members_resolved"], 0.5)
        self.assertEqual(metrics["empty_edge_labels"], 0.5)
        self.assertEqual(metrics["externals"], 1)
        self.assertEqual(metrics["key_file_recall"], 0.5)
        self.assertTrue(any("members_resolved" in f for f in checks.gate("map", metrics)))


class GraphScoringTests(unittest.TestCase):
    def test_dangling_edges_and_orphan_annotations_fail_the_gate(self):
        graph = {
            "nodes": [{"id": "a.py", "type": "file", "summary": "ok", "dependencies": ["gone.py"]}, {"id": "src", "type": "folder", "summary": "", "dependencies": []}],
            "annotations": [{"node_id": "missing.py", "rationale_stated": "s", "rationale_inferred": "i"}],
            "architecture": None,
        }
        metrics = checks.score_graph(graph)
        self.assertEqual(metrics["dangling_dependencies"], 1)
        failures = " ".join(checks.gate("graph", metrics))
        for expected in ("dangling_dependencies", "annotations_both_rationales", "annotations_on_missing_nodes"):
            self.assertIn(expected, failures)


if __name__ == "__main__":
    unittest.main()
