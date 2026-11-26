"""Compile the shipped locked profile and a temporary, compile-only enabled profile."""
import argparse
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--cli',default='arduino-cli');args=parser.parse_args()
    source=ROOT/'firmware/RobotWorkSimulation'
    with tempfile.TemporaryDirectory(prefix='mobot-uno-compile-') as directory:
        root=Path(directory)
        subprocess.run([args.cli,'compile','--fqbn','arduino:avr:uno','--warnings','all','--build-path',str(root/'locked-build'),str(source)],check=True)
        enabled=root/'RobotWorkSimulation';shutil.copytree(source,enabled)
        profile=enabled/'hardware_profile.h';text=profile.read_text(encoding='utf-8-sig')
        text=text.replace('TIMING_PROFILE_APPROVED = false','TIMING_PROFILE_APPROVED = true')
        # Test constants keep live branches in the compiled object. Never upload this build.
        values={'PUSH_MS':100,'RETRACT_MS':100,'REVERSAL_MS':50,'IR1_DELAY_MS':0,'IR2_DELAY_MS':0,'TRAVEL_LIMIT_MS':5000,'HEARTBEAT_LIMIT_MS':2000,'SENSOR_FILTER_MS':20,'STEP_HALF_PERIOD_US':80}
        for key,value in values.items():text=re.sub(r'(constexpr unsigned long '+key+r' = )\d+',r'\g<1>'+str(value),text)
        profile.write_text(text,encoding='utf-8')
        subprocess.run([args.cli,'compile','--fqbn','arduino:avr:uno','--warnings','all','--build-path',str(root/'enabled-build'),str(enabled)],check=True)
    print('PASS: UNO locked and compile-only enabled builds. No test firmware retained or uploaded.')


if __name__=='__main__':main()
