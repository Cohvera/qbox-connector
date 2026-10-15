#!/usr/bin/env python3
"""Inspect an official HFSQL Linux driver; optionally test loading, never connect."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import platform
import struct
import subprocess
import sys

MACHINES = {3:'x86',62:'x86_64',183:'aarch64'}


def architecture(path):
    with Path(path).open('rb') as source:
        header=source.read(64)
    if len(header)<20 or header[:4] != b'\x7fELF' or header[4] not in (1,2) or header[5] not in (1,2):
        raise ValueError('Geen geldig Linux ELF-driverbestand.')
    machine=struct.unpack(('<' if header[5]==1 else '>')+'H',header[18:20])[0]
    return {'architecture':MACHINES.get(machine,'unknown-'+str(machine)), 'bits':32 if header[4]==1 else 64}


def inspect(path):
    info=architecture(path)
    host=platform.machine().lower()
    host={'arm64':'aarch64','amd64':'x86_64'}.get(host,host)
    return {**info,'host_architecture':host,'host_os':platform.system(),
            'native_compatible':platform.system()=='Linux' and info['architecture']==host and info['bits']==ctypes.sizeof(ctypes.c_void_p)*8}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('driver',type=Path)
    parser.add_argument('--load',action='store_true',help='Laad alleen een officiële vertrouwde driver, zonder databaseverbinding.')
    args=parser.parse_args()
    path=args.driver.resolve(strict=True)
    report=inspect(path)
    if args.load:
        if not report['native_compatible']:
            report['load']='niet uitgevoerd: besturingssysteem of architectuur verschilt'
        else:
            # Fresh subprocess: the loader reads LD_LIBRARY_PATH before Python starts.
            environment={**os.environ,'LD_LIBRARY_PATH':str(path.parent)}
            probe='import ctypes,sys; d=ctypes.CDLL(sys.argv[1]); [getattr(d,n) for n in ("SQLAllocHandle","SQLDriverConnect","SQLExecDirect")]'
            try:
                result=subprocess.run([sys.executable,'-c',probe,str(path)],env=environment,capture_output=True,text=True,timeout=20)
                report['load']='PASS' if result.returncode==0 else 'FAIL'
                if result.returncode:report['detail']=result.stderr[-2000:]
            except subprocess.TimeoutExpired:
                report['load']='FAIL: tijdslimiet'
    report['database_test']='niet uitgevoerd; laden bewijst nog geen HFSQL-uitlezing'
    print(json.dumps(report,indent=2))
    return 0 if report['native_compatible'] and (not args.load or report.get('load')=='PASS') else 2


if __name__=='__main__':
    try:sys.exit(main())
    except (OSError,ValueError) as error:
        print(str(error),file=sys.stderr);sys.exit(1)
