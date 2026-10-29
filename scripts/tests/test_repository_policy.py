import importlib.util
from pathlib import Path
import unittest


module_path = Path(__file__).resolve().parents[1] / "check_repository.py"
spec = importlib.util.spec_from_file_location("repository_policy", module_path)
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


class PullRequestRoutes(unittest.TestCase):
    def test_same_repository_feature_targets_alpha(self):
        policy.validate_pr("Alpha", "feature/member-calendar", policy.REPOSITORY, ["src/calendar.cs"])

    def test_fork_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Fork"):
            policy.validate_pr("Alpha", "feature/calendar", "someone/fork", ["src/calendar.cs"])

    def test_feature_cannot_skip_to_stable_branches(self):
        for base in ("Beta", "Release", "main", "alpha"):
            with self.subTest(base=base), self.assertRaises(ValueError):
                policy.validate_pr(base, "feature/calendar", policy.REPOSITORY, ["src/calendar.cs"])

    def test_promotions_and_backports(self):
        for head, base in (("Alpha", "Beta"), ("Beta", "Release"), ("Release", "Beta"), ("Beta", "Alpha"), ("Release", "Alpha")):
            with self.subTest(head=head, base=base):
                policy.validate_pr(base, head, policy.REPOSITORY, ["src/calendar.cs"])

    def test_alpha_cannot_skip_beta(self):
        with self.assertRaises(ValueError):
            policy.validate_pr("Release", "Alpha", policy.REPOSITORY, ["src/calendar.cs"])

    def test_stabilization_branch_is_supported(self):
        policy.validate_pr("Beta", "fix/member-regression", policy.REPOSITORY, ["src/calendar.cs"])
        policy.validate_pr("Release", "hotfix/member-startup", policy.REPOSITORY, ["src/startup.cs"])

    def test_main_accepts_governance_but_not_application_code(self):
        policy.validate_pr("main", "chore/init-collaboration", policy.REPOSITORY, ["README.md", ".github/workflows/repository-policy.yml"])
        with self.assertRaises(ValueError):
            policy.validate_pr("main", "chore/runtime", policy.REPOSITORY, ["src/app.cs"])

    def test_empty_task_suffix_is_rejected(self):
        with self.assertRaises(ValueError):
            policy.validate_pr("Alpha", "feature/", policy.REPOSITORY, ["README.md"])


class VersionTags(unittest.TestCase):
    def test_stage_numbers(self):
        for name, stage in (("0.0.1V", "Alpha"), ("0.0.10V", "Alpha"), ("0.1.0V", "Beta"), ("0.12.3V", "Beta"), ("1.0.0V", "Release"), ("1.2.3V", "Release")):
            with self.subTest(name=name):
                self.assertEqual(policy.tag_stage(name), stage)

    def test_ambiguous_and_unsupported_tags_are_rejected(self):
        for name in ("0.0.1 V", "v1.0.0", "1.0.0v", "2.0.0V", "0.01.0V", "0.0.01V", "0.0.xV"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                policy.tag_stage(name)


class TrackedArtifacts(unittest.TestCase):
    def test_local_data_and_generated_artifacts_are_rejected(self):
        for name in (".env", "server/.env.production", "Storage/app.sqlite3", "Storage/app.sqlite3-wal", "src/obj/file.json", "backups/schema.sql", "keys/server.pfx"):
            with self.subTest(name=name):
                self.assertTrue(policy.forbidden_file(name))

    def test_examples_sql_and_documents_are_preserved(self):
        for name in (".env.example", "server/.env.production.example", "db/schema.sql", "docs/design.md", "docs/spec.docx", "src/app.cs"):
            with self.subTest(name=name):
                self.assertFalse(policy.forbidden_file(name))


if __name__ == "__main__":
    unittest.main()
