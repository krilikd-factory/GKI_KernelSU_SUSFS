#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / ".github/actions/adios/patches/0001-linux6.12-ADIOS-3.3.0.patch"
PATCH_SHA256 = "ebcd195e0715165f4cc27fcbf690f325150b47d75fcdf29719fc8a5158815af8"


class AdiosIntegration(unittest.TestCase):
    def test_reviewed_patch_and_android_makefile(self):
        raw = PATCH.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), PATCH_SHA256)
        text = raw.decode()
        self.assertIn('#define ADIOS_VERSION "3.3.0"', text)
        self.assertNotIn('/lib/modules/$(shell uname -r)', text)
        paths = [line.split()[2][2:] for line in text.splitlines() if line.startswith('diff --git a/')]
        self.assertEqual(paths, ['block/Kconfig.iosched', 'block/Makefile', 'block/adios.c', 'block/elevator.c'])

    def test_fail_closed_apply_and_config(self):
        script = (ROOT / '.github/scripts/adios_apply.sh').read_text()
        self.assertIn('git apply --check "$PATCH"', script)
        self.assertIn('git apply "$PATCH"', script)
        self.assertNotIn('|| true', script)
        self.assertNotIn('patch -p', script)
        action = (ROOT / '.github/actions/adios/action.yml').read_text()
        self.assertIn('CONFIG_MQ_IOSCHED_ADIOS=y', action)
        self.assertIn('CONFIG_MQ_IOSCHED_DEFAULT_ADIOS=n', action)
        build = (ROOT / '.github/workflows/build.yml').read_text()
        self.assertIn("contains(inputs.feature_set, 'ADIOS') && inputs.version == 'android16-6.12'", build)
        self.assertLess(build.index('Apply Performance Patches'), build.index('Apply ADIOS I/O scheduler'))
        self.assertLess(build.index('Apply ADIOS I/O scheduler'), build.index('Apply BORE scheduler'))

    def test_dispatch_and_family_scope(self):
        main = (ROOT / '.github/workflows/main.yml').read_text()
        self.assertIn('- bore_adios', main)
        self.assertIn('F_BORE=true;   F_ADIOS=true', main)
        self.assertIn('[ "$F_ADIOS" = "true" ] && FEATURES="${FEATURES}ADIOS+"', main)
        self.assertIn('FEATURES_NO_BORE="${FEATURES_NO_BORE//ADIOS+/}"', main)
        self.assertIn("USE_ADIOS: ${{ contains(needs.resolve-sources.outputs.feature_set, 'ADIOS') }}", main)

    def test_apply_script_rejects_non_612_before_touching_tree(self):
        with tempfile.TemporaryDirectory() as td:
            tree = Path(td)
            (tree / 'block').mkdir()
            for name in ('Kconfig.iosched', 'Makefile', 'elevator.c'):
                (tree / 'block' / name).write_text('unchanged\n')
            (tree / 'Makefile').write_text('VERSION = 6\nPATCHLEVEL = 18\n')
            env = dict(os.environ)
            proc = subprocess.run(['bash', str(ROOT / '.github/scripts/adios_apply.sh'), str(tree), str(PATCH)],
                                  text=True, capture_output=True, env=env)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn('wired for Linux 6.12 only', proc.stderr)
            self.assertEqual((tree / 'block/Makefile').read_text(), 'unchanged\n')


if __name__ == '__main__':
    unittest.main()
