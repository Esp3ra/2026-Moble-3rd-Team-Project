"""Verify the deployment archive independently of the source checkout."""
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.package_line import build


class LinePackageTests(unittest.TestCase):
    def test_bundle_imports_without_checkout_and_contains_only_shipped_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            target=build(Path(directory)/'line.zip')
            with zipfile.ZipFile(target) as archive:
                names=archive.namelist()
                self.assertIn('factory_v1/account_policy.py',names)
                self.assertIn('raspi_line/config.example.json',names)
                self.assertFalse(any(name.endswith(('config.json','.sqlite3','.pyc','.lock')) for name in names))
            # -I excludes the working directory and PYTHONPATH. Imports must come
            # from the ZIP; no server, USB port or physical motion is started.
            program='''import sys
sys.path.insert(0,sys.argv[1])
import factory_v1.api
import raspi_line.__main__
from factory_v1.devices import create_adapter_api
assert factory_v1.api.__file__.startswith(sys.argv[1])
assert raspi_line.__main__.__file__.startswith(sys.argv[1])
create_adapter_api()
print("PASS: standalone LINE bundle imports and registers device API")
'''
            result=subprocess.run([sys.executable,'-I','-B','-c',program,str(target)],cwd=directory,capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)


if __name__=='__main__':unittest.main()
