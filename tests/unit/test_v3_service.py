import plistlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, call
import os

from quantos.paper import service


class ServiceTests(unittest.TestCase):
    def test_build_plist_arguments_and_supervision(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plist = service.build_plist(root/"venv/bin/python", root/"state/run.db", root/"logs")
        self.assertEqual(plist["Label"], service.LABEL)
        self.assertEqual(plist["ProgramArguments"], [
            os.path.abspath(root/"venv/bin/python"), "-m", "quantos.paper.cli", "start",
            "--mode", "paper", "--state", str((root/"state/run.db").resolve()),
            "--min-quote-volume", "20000000", "--hours", "72"])
        self.assertTrue(plist["RunAtLoad"])
        self.assertEqual(plist["KeepAlive"], {"SuccessfulExit": False})
        self.assertEqual(plist["ExitTimeOut"], 45)
        self.assertEqual(plist["ThrottleInterval"], 30)
        self.assertEqual(Path(plist["WorkingDirectory"]).name, "state")
        self.assertIn("logs", plist["StandardOutPath"])

    def test_duration_must_be_positive(self):
        with self.assertRaises(ValueError): service.build_plist(Path("python"), Path("state"), Path("logs"), 0)
        with self.assertRaises(ValueError): service.build_plist(Path("python"), Path("state"), Path("logs"), 73)

    def test_venv_symlink_path_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base = root/"base-python"
            base.touch()
            venv_python = root/"venv-python"
            venv_python.symlink_to(base)
            plist = service.build_plist(venv_python, root/"state", root/"logs")
            self.assertEqual(plist["ProgramArguments"][0], str(venv_python))
            self.assertNotEqual(plist["ProgramArguments"][0], str(base))

    @patch.object(service, "_domain", return_value="gui/501")
    @patch.object(service, "_launchctl", return_value="/bin/launchctl")
    def test_install_writes_atomic_valid_plist_and_bootstraps_exact_file(self, launchctl, domain):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)/"home"
            state = Path(temp)/"data"/"state.db"
            python = Path(temp)/"venv/bin/python"
            python.parent.mkdir(parents=True)
            python.write_text("#!/bin/sh\nexit 0\n")
            python.chmod(0o755)
            absent = type("Result", (), {"returncode":113,"stderr":"Could not find service","stdout":""})()
            with patch.object(Path, "home", return_value=home), patch.object(service.subprocess, "run") as run:
                run.side_effect = [absent, type("Result", (), {"returncode":0,"stderr":"","stdout":""})()]
                result = service.install_service(python, state, 12)
            self.assertEqual(result, home/"Library/LaunchAgents"/f"{service.LABEL}.plist")
            with result.open("rb") as f: plist = plistlib.load(f)
            self.assertEqual(plist["ProgramArguments"][-1], "12")
            self.assertTrue((state/"logs").is_dir())
            self.assertEqual((state/"logs").stat().st_mode & 0o777, 0o700)
            self.assertEqual(run.call_args_list, [
                call(["/bin/launchctl", "print", f"gui/501/{service.LABEL}"], check=False, capture_output=True, text=True),
                call(["/bin/launchctl", "bootstrap", "gui/501", str(result)], check=True, capture_output=True, text=True)])
            self.assertFalse(list(result.parent.glob("*.tmp")))

    @patch.object(service, "_domain", return_value="gui/501")
    @patch.object(service, "_launchctl", return_value="/bin/launchctl")
    def test_install_refuses_loaded_service_before_writing(self, launchctl, domain):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)/"home"
            python = Path(temp)/"python"
            python.write_text("x")
            python.chmod(0o755)
            loaded = type("Result", (), {"returncode":0,"stderr":"","stdout":""})()
            with patch.object(Path, "home", return_value=home), patch.object(service.subprocess, "run", return_value=loaded):
                with self.assertRaises(RuntimeError): service.install_service(python, Path(temp)/"state")
            self.assertFalse((home/"Library/LaunchAgents"/f"{service.LABEL}.plist").exists())

    @patch.object(service, "_domain", return_value="gui/501")
    @patch.object(service, "_launchctl", return_value="/bin/launchctl")
    def test_status_stop_target_only_fixed_label(self, launchctl, domain):
        with patch.object(service.subprocess, "run", side_effect=[
                type("Result", (), {"returncode":0})(),
                type("Result", (), {"returncode":0,"stderr":"","stdout":""})()]) as run:
            self.assertTrue(service.status_service())
            self.assertTrue(service.stop_service())
        self.assertEqual(run.call_args_list, [
            call(["/bin/launchctl", "print", f"gui/501/{service.LABEL}"], check=False, capture_output=True, text=True),
            call(["/bin/launchctl", "bootout", f"gui/501/{service.LABEL}"], check=False, capture_output=True, text=True)])

    @patch.object(service, "_launchctl", return_value="/bin/launchctl")
    @patch.object(service, "_domain", return_value="gui/501")
    def test_stop_is_idempotent_when_service_absent(self, domain, launchctl):
        result = type("Result", (), {"returncode":113,"stderr":"Could not find service","stdout":""})()
        with patch.object(service.subprocess, "run", return_value=result):
            self.assertFalse(service.stop_service())


if __name__ == "__main__": unittest.main()
