"""Run the production spawn regression with a private temporary config/cache."""
import json,os,subprocess,sys,tempfile
from pathlib import Path
with tempfile.TemporaryDirectory(prefix='pdf2rea-spawn-') as directory:
    root=Path(directory);config=root/'pdf2rea.json';config.write_text(json.dumps({'run_id':'spawn-regression','terminology':{'reuse_ledger':False}}))
    env=os.environ.copy();env.update(PDF2REA_CONFIG=str(config),PDF2REA_CACHE_DIR=str(root/'cache'),PDF2REA_LEDGER_PATH=str(root/'ledger.json'))
    env.pop('PDF2REA_APPLY_EAGERLY',None);env.pop('PDF2REA_ENGINE_ACTIVE',None)
    result=subprocess.run([sys.executable,str(Path(__file__).with_name('test_spawn_runtime.py')),'--input'],env=env,timeout=90)
    raise SystemExit(result.returncode)
