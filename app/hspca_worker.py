"""Isolated HSPCA decoding; same progress protocol as the E57 worker."""
from pathlib import Path
import json
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime'/'hspca_reader_v025'))
from e57_worker import emit

def main():
    try:
        import hspca_import
    except ImportError as exc:
        raise ValueError('Native Leica reader dependency is missing. Run the 0.13.9 update again with internet access.') from exc
    mode,source=sys.argv[1:3]
    progress=lambda message:emit('PROGRESS',message)
    if mode=='describe':
        count,_,_=hspca_import.describe(source,progress);result=[[None,count]]
    elif mode=='import':
        result=hspca_import.import_one(source,sys.argv[3],progress)
    elif mode=='sample':
        result=hspca_import.sample_setups(json.load(sys.stdin),int(source))
    else:raise ValueError('Unknown native reader operation.')
    emit('RESULT',result)

if __name__=='__main__':
    try:main()
    except Exception as exc:
        emit('ERROR',str(exc));sys.exit(1)
