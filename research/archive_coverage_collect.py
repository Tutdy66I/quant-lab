"""Manual bounded official-archive collection; never an API or service fallback."""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from research.archive_normalizer import ROOT, SOURCE, sha, json_bytes, load_evidence


def collect(output, protocol_path):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    (output/'raw').mkdir()
    protocol_blob = Path(protocol_path).read_bytes()
    protocol = json.loads(protocol_blob)
    (output/'protocol.json').write_bytes(protocol_blob)
    records, _ = load_evidence(SOURCE)
    receipts = []

    def fetch(url, name, reuse=True):
        relative = 'raw/'+name
        if reuse and relative in records:
            receipt, blob = records[relative]
            receipt = dict(receipt, reused_from='data/archive_feasibility/binance_um_btcusdt_v1')
        else:
            started = datetime.now(timezone.utc).isoformat()
            try:
                with urlopen(Request(url,headers={'User-Agent':'quant-lab-coverage/1'}),timeout=45) as response:
                    status,blob,headers,final = response.status,response.read(),dict(response.headers),response.url
            except HTTPError as error:
                status,blob,headers,final = error.code,error.read(),dict(error.headers),error.url
            except (OSError,URLError) as error:
                status,blob,headers,final = None,str(error).encode(),{},url
            receipt = {'url':url,'final_url':final,'file':relative,'status':status,'headers':headers,
                'request_started_at_utc':started,'collected_at_utc':datetime.now(timezone.utc).isoformat(),
                'bytes':len(blob),'sha256':sha(blob),'published_at_utc':None}
        (output/relative).write_bytes(blob)
        receipts.append(receipt)
        (output/'receipts.json').write_bytes(json_bytes(receipts))
        print(receipt['status'],name,len(blob),'reused' if 'reused_from' in receipt else '',flush=True)
        if receipt['status'] in (None,401,403,429,451) or receipt['status'] >= 500:
            raise RuntimeError('Stop on access restriction/failure; no retry or alternate host')
        return receipt['status'] == 200

    fetch('https://raw.githubusercontent.com/binance/binance-public-data/master/TERMS_AND_CONDITIONS.md','current_terms.md',False)
    dates = protocol['dates']
    references = sorted(set(dates)|{(datetime.fromisoformat(d)-timedelta(days=1)).date().isoformat() for d in dates})
    for kind,days in [('metrics',dates),('klines',references)]:
        for day in days:
            name = f'BTCUSDT-metrics-{day}.zip' if kind == 'metrics' else f'BTCUSDT-5m-{day}.zip'
            folder = 'metrics/BTCUSDT/' if kind == 'metrics' else 'klines/BTCUSDT/5m/'
            url = 'https://data.binance.vision/data/futures/um/daily/'+folder+name
            if fetch(url,name):
                fetch(url+'.CHECKSUM',name+'.CHECKSUM')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--protocol',type=Path,default=ROOT/'docs/archive_coverage_protocol_v1.json')
    args=parser.parse_args()
    collect(args.output,args.protocol)
