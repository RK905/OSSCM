#!/usr/bin/env python3
"""Compare every public application table after restoring a custom-format pg_dump.
Usage: python scripts/verify_restore.py SOURCE_DATABASE_URL RESTORED_DATABASE_URL
Run against quiesced databases. This script is read-only and never prints row contents.
"""
import hashlib
import json
import sys
import psycopg
from psycopg import sql


def snapshot(url):
    output={}
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
            tables=[row[0] for row in cur.fetchall()]
        for table in tables:
            digest=hashlib.sha256();count=0
            with conn.cursor(name='restore_verify') as cur:
                cur.execute(sql.SQL('SELECT row_to_json(t)::text FROM {} t ORDER BY row_to_json(t)::text').format(sql.Identifier(table)))
                for row in cur:
                    digest.update(row[0].encode()+b'\n');count+=1
            output[table]={'rows':count,'sha256':digest.hexdigest()}
    return output


if __name__=='__main__':
    if len(sys.argv)!=3:raise SystemExit(__doc__)
    before,after=map(snapshot,sys.argv[1:])
    differences=[table for table in set(before)|set(after) if before.get(table)!=after.get(table)]
    print(json.dumps({'verified_tables':len(before),'rows':sum(v['rows'] for v in before.values()),'match':not differences,'different_tables':differences},indent=2))
    raise SystemExit(1 if differences else 0)
