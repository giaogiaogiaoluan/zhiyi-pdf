#!/usr/bin/env python3
"""Start only the marked 知译 PDF runtime; never touch the stock PDF2zh server."""
from __future__ import annotations
import argparse
import fcntl
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import urllib.request

OWNER='pdf2rea@qoder.dev'


def health(root, url):
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url+'/health',timeout=.8) as response:
            data=json.load(response)
        if data.get('status')=='ok' and Path(data.get('outputDir','')).resolve()==(root/'translated').resolve():
            return {'ok':True,'status':'running','version':data.get('version'),'outputDir':data['outputDir']}
        return {'ok':False,'status':'wrong_service','message':'该地址不属于此 知译 PDF 独立环境'}
    except Exception:
        return {'ok':False,'status':'stopped','message':'知译 PDF 独立服务未启动'}


def control(root, action):
    marker=json.loads((root/'pdf2rea-runtime.json').read_text())
    if marker.get('owner')!=OWNER or root.is_symlink():raise ValueError('拒绝操作未标记的独立环境')
    venv=root/'zotero-pdf2zh-next-venv'
    if venv.is_symlink():raise ValueError('独立环境不能是原版环境的链接')
    port=int(marker.get('server_port',8891));url=f'http://127.0.0.1:{port}'
    with (root/'pdf2rea-service.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        result=health(root,url)
        if action=='status' or result['ok'] or result['status']=='wrong_service':return result
        with socket.socket() as probe:
            probe.settimeout(.5)
            if probe.connect_ex(('127.0.0.1',port))==0:
                return {'ok':False,'status':'port_busy','message':f'{port} 端口被其他程序占用'}
        python=venv/'bin/python';runner=root/'run_pdf2rea.py'
        if not python.is_file() or not runner.is_file():raise ValueError('独立环境不完整，请运行项目中的启动入口准备环境')
        env=os.environ.copy();env['PYTHONDONTWRITEBYTECODE']='1';env.pop('PYTHONPATH',None)
        with (root/'pdf2rea-service.log').open('a') as log:
            child=subprocess.Popen([str(python),'-B',str(runner),'--port',str(port)],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,cwd=root,env=env,start_new_session=True,close_fds=True)
        (root/'pdf2rea-service.pid').write_text(str(child.pid)+'\n')
        deadline=time.monotonic()+40
        while time.monotonic()<deadline:
            result=health(root,url)
            if result['ok']:result['started']=True;return result
            if child.poll() is not None:
                return {'ok':False,'status':'failed','message':f'服务启动失败，日志：{root / "pdf2rea-service.log"}'}
            time.sleep(.3)
        return {'ok':False,'status':'starting','message':'服务仍在启动，稍后点“启动/重连”重试'}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['start','status']);parser.add_argument('--runtime',required=True,type=Path);args=parser.parse_args()
    try:result=control(args.runtime.expanduser().absolute(),args.action)
    except Exception as e:result={'ok':False,'status':'error','message':str(e)}
    print(json.dumps(result,ensure_ascii=False));return 0 if result['ok'] else 1

if __name__=='__main__':raise SystemExit(main())
