"""Validate Windows task plans without registering, enabling or starting tasks."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET


@unittest.skipUnless(os.name == 'nt', 'Windows Task Scheduler plan validation')
class ArchiveTaskPlanTests(unittest.TestCase):
    def test_plan_roundtrips_paths_and_validates_in_native_scheduler(self):
        script = Path(__file__).resolve().parents[1] / 'deployment/archive-service.ps1'
        with tempfile.TemporaryDirectory(prefix="archive task ' ") as temporary:
            data = str(Path(temporary) / 'data & evidence')
            result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive',
                '-ExecutionPolicy', 'RemoteSigned', '-File', str(script), '-Action', 'Plan',
                '-PythonExe', sys.executable, '-DataRoot', data], capture_output=True, text=True, check=True)
            root = ET.fromstring(result.stdout.strip())
            ns = {'t': 'http://schemas.microsoft.com/windows/2004/02/mit/task'}
            find = lambda name: root.find('.//t:' + name, ns).text
            self.assertEqual(find('Interval'), 'PT1H')
            self.assertEqual(find('MultipleInstancesPolicy'), 'IgnoreNew')
            self.assertEqual(find('StartWhenAvailable'), 'true')
            self.assertEqual(find('WakeToRun'), 'false')
            self.assertEqual(find('LogonType'), 'InteractiveToken')
            self.assertEqual(find('RunLevel'), 'LeastPrivilege')
            self.assertEqual(find('ExecutionTimeLimit'), 'PT20M')
            # Windows runners can expand TEMP's 8.3 alias (RUNNER~1) to runneradmin.
            # Compare filesystem identity, while still checking quoted special characters.
            arguments = find('Arguments')
            actual_data = arguments.rsplit(' -DataRoot "', 1)[1][:-1]
            self.assertEqual(Path(data).resolve(), Path(actual_data).resolve())
            self.assertIn("archive task ' ", actual_data)
            self.assertTrue(actual_data.endswith('data & evidence'))
            self.assertIsNotNone(root.find('.//t:LogonTrigger', ns))
            xml = Path(temporary) / 'task.xml'
            xml.write_text(result.stdout.strip(), encoding='utf-16')
            check = Path(temporary) / 'validate.ps1'
            check.write_text('param($XmlPath)\n$ErrorActionPreference="Stop"\n'
                '$service=New-Object -ComObject Schedule.Service\n$service.Connect()\n'
                '$task=$service.NewTask(0)\n$task.XmlText=[IO.File]::ReadAllText($XmlPath)\n'
                'if ($task.Triggers.Count -ne 2) { throw "Invalid trigger count" }\n'
                'Write-Output "Native scheduler definition validated; nothing registered"\n', encoding='utf-8')
            parsed = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive',
                '-ExecutionPolicy', 'RemoteSigned', '-File', str(check), str(xml)],
                capture_output=True, text=True, check=True)
            self.assertIn('nothing registered', parsed.stdout)

    def test_install_requires_approval_and_handover_before_any_registration(self):
        script = Path(__file__).resolve().parents[1] / 'deployment/archive-service.ps1'
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive',
            '-ExecutionPolicy', 'RemoteSigned', '-File', str(script), '-Action', 'Install',
            '-PythonExe', sys.executable], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Install requires user approval', result.stderr)


if __name__ == '__main__':
    unittest.main()
