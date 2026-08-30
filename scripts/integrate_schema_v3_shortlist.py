from __future__ import annotations

import argparse
import json
from pathlib import Path

from portfolio_advisor.database.migrations.shortlist_parallel import integrate_shortlist


def main() -> int:
 p=argparse.ArgumentParser(); p.add_argument('--apply',action='store_true'); p.add_argument('--workbooks',type=Path,default=Path('data/xls/processed')); p.add_argument('--target',type=Path,default=Path('database/portfolio_advisor.sqlite')); p.add_argument('--output',type=Path,default=Path('data/audit/milestone_9_shortlist_relational_import.json')); a=p.parse_args(); r=integrate_shortlist(workbook_directory=a.workbooks,target=a.target,apply=a.apply); a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps({**r,'mode':'APPLY' if a.apply else 'DRY_RUN','cutover_status':'NOT_AUTHORIZED'},indent=2,sort_keys=True)+'\n'); return 0
if __name__=='__main__': raise SystemExit(main())
