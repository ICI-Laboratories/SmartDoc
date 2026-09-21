"""Private operator report. Run inside the library container; no public endpoint."""
import argparse
import json
from .db import make_pool


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--days',type=int,default=7,choices=range(1,366),metavar='1..365')
    args=parser.parse_args()
    with make_pool() as pool:
        with pool.connection() as conn:
            rows=conn.execute('''SELECT event,source,value,count(*) AS total,
                count(DISTINCT visitor) AS visitors,
                count(*) FILTER (WHERE status>=400) AS errors,
                round(avg(duration_ms)) AS avg_ms,
                percentile_disc(0.95) WITHIN GROUP (ORDER BY duration_ms) AS p95_ms
                FROM usage_events WHERE occurred_at>=now()-%s*interval '1 day'
                GROUP BY event,source,value ORDER BY total DESC''',(args.days,)).fetchall()
    print(json.dumps({'days':args.days,'events':rows},ensure_ascii=False,indent=2,default=str))


if __name__=='__main__':
    main()
