import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch, Mock
from zoneinfo import ZoneInfo

spec=importlib.util.spec_from_file_location('quant_bridge',Path(__file__).resolve().parents[1]/'scripts/wind_quant_bridge.py')
bridge=importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class BridgeTest(unittest.TestCase):
    def at(self,time):
        return datetime.fromisoformat('2026-09-14T'+time).replace(tzinfo=ZoneInfo('Asia/Shanghai'))

    def test_late_trigger_cannot_start_new_selection(self):
        self.assertEqual(bridge.phase_for(self.at('14:50'),self.at('14:54')),'window')
        self.assertIsNone(bridge.phase_for(self.at('14:50'),self.at('14:55')))
        self.assertIsNone(bridge.phase_for(self.at('09:30'),self.at('09:35')))

    def test_target_project_environment_and_interpreter(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve()
            command=root/'backend/scripts/wind_quant_agent.py'
            command.parent.mkdir(parents=True);command.touch()
            python=root/('.venv/Scripts/python.exe' if os.name=='nt' else '.venv/bin/python')
            python.parent.mkdir(parents=True);python.touch()
            clock=Mock();clock.now.return_value=self.at('14:51')
            with patch.object(bridge,'datetime',clock), patch.dict(os.environ,{'KSTOCK_WIND_QUANT_PROJECT_ROOT':str(root)}), patch.object(bridge.subprocess,'Popen',return_value=Mock(pid=123)) as launch:
                result=bridge.start_quant_worker(self.at('14:50'),'nonexistent-project')
                self.assertEqual(result['status'],'worker_started')
                args=launch.call_args.args[0]
                self.assertEqual(args[0],str(python))
                self.assertEqual(args[1],str(command))
                self.assertEqual(launch.call_args.kwargs['cwd'],root)
                self.assertNotIn('shell',launch.call_args.kwargs)
