"""Compile actual UNO source against a host Arduino stub; no USB/upload/motors."""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def main():
    source=ROOT/'tests/firmware/conveyor_test.cpp';include=ROOT/'tests/firmware'
    with tempfile.TemporaryDirectory(prefix='mobot-conveyor-host-') as folder:
        target=Path(folder)/('check.exe' if os.name=='nt' else 'check')
        if shutil.which('g++'):
            subprocess.run(['g++','-std=c++17','-I',str(include),str(source),'-o',str(target)],check=True)
        elif os.name=='nt':
            vswhere=Path(os.environ.get('ProgramFiles(x86)',r'C:\Program Files (x86)'))/'Microsoft Visual Studio/Installer/vswhere.exe'
            installation=subprocess.check_output([str(vswhere),'-latest','-products','*','-requires','Microsoft.VisualStudio.Component.VC.Tools.x86.x64','-property','installationPath'],text=True).strip()
            if not installation:raise RuntimeError('MSVC C++ compiler not found')
            setup=Path(installation)/'Common7/Tools/VsDevCmd.bat'
            environment=subprocess.check_output(f'cmd.exe /d /c call "{setup}" -no_logo -arch=x64 >nul && set',text=True)
            env={key.upper():value for key,value in os.environ.items()}
            for line in environment.splitlines():
                if '=' in line and not line.startswith('='):
                    key,value=line.split('=',1);env[key.upper()]=value
            compiler=shutil.which('cl.exe',path=env.get('PATH'))
            if not compiler:raise RuntimeError('MSVC setup did not expose cl.exe')
            subprocess.run([compiler,'/nologo','/std:c++17','/EHsc','/I'+str(include),str(source),'/Fe:'+str(target),'/Fo:'+str(Path(folder)/'check.obj')],env=env,check=True)
        else:raise RuntimeError('g++ is required')
        subprocess.run([str(target)],check=True)


if __name__=='__main__':main()
