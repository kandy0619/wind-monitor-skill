"""Optional monitor poll hook; starts a hidden, DB-leased paper-data worker."""
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
from zoneinfo import ZoneInfo


def phase_for(planned,actual):
    if planned.weekday()>=5 or planned.date()!=actual.date():return None
    slot=planned.strftime('%H:%M');now=actual.strftime('%H:%M')
    if slot=='14:50' and '14:50'<=now<'14:55':return 'window'
    if slot=='09:30' and '09:30'<=now<'09:35':return 'open_window'
    if slot=='15:00' and now>='15:00':return 'close_marks'
    if slot=='15:10' and now>='15:10':return 'close'
    return None


def start_quant_worker(planned,project_root):
    actual=datetime.now(ZoneInfo('Asia/Shanghai'))
    planned=planned.astimezone(ZoneInfo('Asia/Shanghai'))
    phase=phase_for(planned,actual)
    if not phase:return {'status':'not_a_quant_phase'}
    settings=Path(__file__).resolve().parents[1]/'references/wind-quant-local.json'
    cfg={}
    if settings.exists():
        cfg=json.loads(settings.read_text(encoding='utf-8'))
        if not cfg.get('enabled'):return {'status':'bridge_disabled'}
    root=Path(os.environ.get('KSTOCK_WIND_QUANT_PROJECT_ROOT') or cfg.get('project_root') or project_root).resolve()
    command=root/'backend/scripts/wind_quant_agent.py'
    if not command.exists():return {'status':'quant_code_not_installed'}
    logs=root/'logs/wind-quant';logs.mkdir(parents=True,exist_ok=True)
    target=logs/(actual.strftime('%Y%m%d-%H%M%S')+'-'+phase+'.log')
    # Process output is only a redacted receipt/error type. No shell or credential args.
    python=root/('.venv/Scripts/python.exe' if os.name=='nt' else '.venv/bin/python')
    executable=str(python) if python.is_file() else sys.executable
    with target.open('ab') as stream:
        child=subprocess.Popen([executable,str(command),'--project-root',str(root),'--phase',phase],
              cwd=root,stdin=subprocess.DEVNULL,stdout=stream,stderr=stream,
              creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,
              start_new_session=os.name!='nt')
    return {'status':'worker_started','phase':phase,'pid':child.pid,'paper_only':True}
