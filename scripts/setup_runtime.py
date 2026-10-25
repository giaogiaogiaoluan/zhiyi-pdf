#!/usr/bin/env python3
"""Prepare a separate local ZhiYi PDF server. Reads stock files; never writes there.

macOS uses APFS copy-on-write where supported. No credentials, translated papers,
Zotero profile or library databases are copied. No packages are downloaded.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

OWNER = 'pdf2rea@qoder.dev'
ROOT = Path(__file__).resolve().parent.parent


def clone_tree(source: Path, target: Path) -> None:
    if target.exists(): raise SystemExit(f'目标已存在，拒绝覆盖：{target}')
    if sys.platform == 'darwin':
        result = subprocess.run(['/bin/cp', '-cR', str(source), str(target)], capture_output=True)
        if result.returncode == 0: return
        if target.exists(): shutil.rmtree(target)
    shutil.copytree(source, target, symlinks=True)


def copy_server_dependencies(target_python: Path, target_site: Path) -> None:
    # The original server uses an ephemeral uv environment. Copy its installed
    # dependencies into the NEW environment instead of running pip in the stock one.
    probe = '''import importlib.metadata as m,json
names=['flask','blinker','click','itsdangerous','jinja2','markupsafe','werkzeug','toml']
result=[]
for name in names:
 d=m.distribution(name)
 for f in d.files or []:
  if '..' not in f.parts and d.locate_file(f).is_file():result.append([str(f),str(d.locate_file(f))])
print(json.dumps(result))'''
    candidates = [target_python]
    candidates += sorted((Path.home()/'.cache/uv/builds-v0').glob('*/bin/python'), reverse=True)
    for interpreter in candidates:
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PDF2REA_DISABLE='1')
        result = subprocess.run([str(interpreter), '-B', '-c', probe], capture_output=True, text=True, env=env)
        if result.returncode: continue
        for relative, source in json.loads(result.stdout):
            target = target_site / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            # Never follow a copied symlink into a stock file when replacing it.
            if target.is_symlink(): target.unlink()
            if Path(source).resolve() != target.resolve(): shutil.copy2(source, target)
        return
    raise SystemExit('未找到带 Flask/TOML 的已安装服务环境。已保留独立副本；请在其 venv 中单独安装 server/requirements.txt 后使用。')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path.home()/'zotero-pdf2zh/server')
    parser.add_argument('--destination', type=Path, default=Path.home()/'.pdf2rea/server')
    args = parser.parse_args()
    source, target = args.source.expanduser().resolve(), args.destination.expanduser().resolve()
    if source == target or target.is_relative_to(source) or source.is_relative_to(target):
        raise SystemExit('原版目录与独立目录必须完全分开')
    if target.exists(): raise SystemExit(f'目标已存在，拒绝覆盖：{target}')
    original_venv = source/'zotero-pdf2zh-next-venv'
    if not (source/'server.py').is_file() or not (original_venv/'bin/python').exists():
        raise SystemExit('未找到可供只读复制的 server 与 pdf2zh_next 环境')
    target.mkdir(parents=True)
    for name in ['server.py','index.html','favicon.svg','bo.mp3','requirements.txt','notice.json']:
        if (source/name).is_file(): shutil.copy2(source/name, target/name)
    shutil.copytree(source/'utils', target/'utils', ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    (target/'config').mkdir()
    for path in (source/'config').glob('*.example'):
        shutil.copy2(path, target/'config'/path.name)
        shutil.copy2(path, target/'config'/path.name.removesuffix('.example'))
    (target/'translated').mkdir()
    venv = target/original_venv.name
    print('复制独立引擎环境（原版只读）…', flush=True)
    clone_tree(original_venv, venv)
    # Relocate entry scripts. The Python executable remains a symlink to the
    # shared interpreter binary, while pyvenv.cfg and all packages are private.
    for path in (venv/'bin').iterdir():
        if path.is_symlink() or not path.is_file(): continue
        data = path.read_bytes()
        if data.startswith(b'#!') or path.name.startswith('activate'):
            updated = data.replace(str(original_venv).encode(), str(venv).encode())
            if updated != data: path.write_bytes(updated)
    python = venv/'bin/python'
    sites = sorted((venv/'lib').glob('python*/site-packages'))
    if len(sites) != 1: raise SystemExit('独立环境 site-packages 不唯一')
    site = sites[0]
    for name in ['pdf2rea.pth','pdf2rea_engine']:
        old = site/name
        if old.is_symlink() or old.is_file(): old.unlink()
        elif old.is_dir(): shutil.rmtree(old)
    copy_server_dependencies(python, site)
    # Cache and term-memory writes are also isolated. Copy reusable model assets,
    # not the original translation database or working-paper intermediates.
    cache = target/'cache/babeldoc'
    cache.mkdir(parents=True)
    original_cache = Path.home()/'.cache/babeldoc'
    for name in ['models','fonts','cmap','tiktoken']:
        if (original_cache/name).is_dir(): clone_tree(original_cache/name, cache/name)
    # Adapt only the PRIVATE package copies to respect dedicated cache/config
    # roots. This prevents even cache writes in the original engine's directories.
    replacements = {
      'babeldoc/const.py': ('CACHE_FOLDER = Path.home() / ".cache" / "babeldoc"', 'CACHE_FOLDER = Path(os.environ.get("PDF2REA_CACHE_DIR", str(Path.home() / ".cache" / "babeldoc")))'),
      'pdf2zh_next/const.py': ('DEFAULT_CONFIG_DIR = Path("~/.config/pdf2zh").expanduser()', 'DEFAULT_CONFIG_DIR = Path(__import__("os").environ.get("PDF2REA_CONFIG_DIR", "~/.config/pdf2zh")).expanduser()'),
      'pdf2zh_next/translator/cache.py': ('cache_folder = Path.home() / ".cache" / "pdf2zh_next"', 'cache_folder = Path(__import__("os").environ.get("PDF2REA_TRANSLATOR_CACHE", str(Path.home() / ".cache" / "pdf2zh_next")))'),
    }
    for relative, (before, after) in replacements.items():
        path = site/relative
        contents = path.read_text()
        if before not in contents: raise SystemExit(f'独立缓存锚点变化：{relative}')
        if path.is_symlink(): raise SystemExit(f'拒绝修改链接：{relative}')
        path.write_text(contents.replace(before, after))
    marker = {'owner': OWNER, 'source_read_only': str(source), 'server_port':8891, 'isolated_cache':str(cache)}
    (target/'pdf2rea-runtime.json').write_text(json.dumps(marker,ensure_ascii=False,indent=2)+'\n')
    shutil.copytree(ROOT/'engine/pdf2rea_engine',site/'pdf2rea_engine',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    (site/'pdf2rea.pth').write_text('import pdf2rea_engine.bootstrap\n')
    sys.path.insert(0,str(ROOT/'engine'))
    from pdf2rea_engine.options import DEFAULTS
    (target/'config/pdf2rea.json').write_text(json.dumps(DEFAULTS,ensure_ascii=False,indent=2)+'\n')
    runner = '''#!/usr/bin/env python3
from pathlib import Path
import os,sys
r=Path(__file__).resolve().parent
os.environ['PATH']=str(r/'zotero-pdf2zh-next-venv/bin')+os.pathsep+os.environ.get('PATH','')
os.environ['PDF2REA_CONFIG']=str(r/'config/pdf2rea.json')
os.environ['PDF2REA_CACHE_DIR']=str(r/'cache/babeldoc')
os.environ['PDF2REA_TRANSLATOR_CACHE']=str(r/'cache/pdf2zh_next')
os.environ['PDF2REA_CONFIG_DIR']=str(r/'config/pdf2zh')
os.environ['PDF2REA_LEDGER_PATH']=str(r/'ledger.json')
os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ.pop('PYTHONPATH',None)
os.chdir(r)
os.execv(str(r/'zotero-pdf2zh-next-venv/bin/python'),['python','-B',str(r/'server_compat.py'),'--runtime',str(r),*sys.argv[1:]])
'''
    shutil.copy2(ROOT/'plugin/content/runtime/server_compat.py', target/'server_compat.py')
    (target/'run_pdf2rea.py').write_text(runner)
    launcher = '#!/bin/zsh\nTASK_ROOT="${0:A:h}"\nexec "$TASK_ROOT/zotero-pdf2zh-next-venv/bin/python" -B "$TASK_ROOT/run_pdf2rea.py"\n'
    start = target/'启动 知译 PDF.command';start.write_text(launcher);start.chmod(0o755)
    result = subprocess.run([str(python),'-B','-c','import flask,toml,pymupdf,pdf2zh_next; print("runtime imports OK")'], capture_output=True,text=True,env=dict(os.environ, PDF2REA_DISABLE='1', PYTHONDONTWRITEBYTECODE='1',
      PDF2REA_CACHE_DIR=str(target/'cache/babeldoc'), PDF2REA_TRANSLATOR_CACHE=str(target/'cache/pdf2zh_next'),
      PDF2REA_CONFIG_DIR=str(target/'config/pdf2zh'), PDF2REA_LEDGER_PATH=str(target/'ledger.json')))
    if result.returncode: raise SystemExit(result.stderr)
    print(result.stdout.strip())
    print(f'准备完成：{target}\n双击 {start}\n插件地址：http://127.0.0.1:8891')
    return 0

if __name__ == '__main__': raise SystemExit(main())
