import os
import re
import unittest
from pathlib import Path


ROOT = Path(os.environ.get('NOMOUNT_TEST_ROOT', Path(__file__).resolve().parents[1]))
ACTION = ROOT / ".github/actions/nomount-metamodule/action.yml"
BUILD = ROOT / ".github/actions/nomount-metamodule/build-userspace.sh"
VALIDATE = ROOT / ".github/workflows/validate.yml"


class NoMountUserspaceBuildTests(unittest.TestCase):
    def test_action_uses_tested_toolchain_and_shared_builder(self):
        action = ACTION.read_text(encoding="utf-8")
        self.assertRegex(action, re.compile(r"version:\s*0\.17\.1\s*(?:#.*)?$", re.MULTILINE))
        self.assertNotRegex(action, r"version:\s*master\b")
        self.assertIn('bash "$GITHUB_ACTION_PATH/build-userspace.sh"', action)
        self.assertIn('set -euo pipefail', action)

    def test_commit_and_patch_safety_guards_remain(self):
        action = ACTION.read_text(encoding="utf-8")
        self.assertIn('^[0-9a-f]{40}$', action)
        self.assertIn('git -C "$source_dir" checkout --detach "$expected_commit"', action)
        self.assertIn('if [ "$actual_commit" != "$expected_commit" ]', action)
        self.assertNotIn('|| true', action)

    def test_builder_uses_libc_capable_static_targets(self):
        builder = BUILD.read_text(encoding="utf-8")
        self.assertIn('expected_zig="0.17.1"', builder)
        self.assertIn('-target aarch64-linux-musl', builder)
        self.assertIn('-target arm-linux-musleabi', builder)
        self.assertNotIn('-nostdlib', builder.split('# nm.c includes')[0])
        self.assertNotRegex(re.sub(r'(?m)^\s*#.*$', '', builder), r'-nostdlib|--entry(?:=|\s+)')
        self.assertNotIn('|| true', builder)
        self.assertTrue(os.stat(BUILD).st_mode & 0o111, "builder must be executable")

    def test_ci_compiles_the_same_pinned_source_with_the_same_builder(self):
        validate = VALIDATE.read_text(encoding="utf-8")
        self.assertIn('nomount-userspace-smoke:', validate)
        self.assertIn('NOMOUNT_SMOKE_COMMIT: dcd77d03e9912f397a586d7da81694d1628f170e', validate)
        self.assertRegex(validate, re.compile(r'version:\s*0\.17\.1\s*$', re.MULTILINE))
        self.assertIn('bash .github/actions/nomount-metamodule/build-userspace.sh', validate)
        self.assertIn('git -C "$source_dir" checkout --detach "$NOMOUNT_SMOKE_COMMIT"', validate)
        self.assertIn('rev-parse HEAD', validate)

    def test_builder_inspects_architecture_static_linking_entry_and_mode(self):
        builder = BUILD.read_text(encoding="utf-8")
        for check in ('expected = ((2, 183, "AArch64"), (1, 40, "ARM"))',
                      'PT_DYNAMIC', 'entry == 0', 'os.access(path, os.X_OK)',
                      'hashlib.sha256'):
            self.assertIn(check, builder)


if __name__ == '__main__':
    unittest.main()
