#!/usr/bin/env python3
"""Verify a prebuilt production API image and real backup/restore using LOCAL Docker only.

Requires an explicit unix:// Docker socket and already-cached images. This script
does not build/pull images, start a daemon, publish ports, read existing app/AWS
settings, send SES email, or touch existing stores. It creates a labeled internal
network and four disposable containers, then removes only those resources.

Example::
    python3 scripts/verify_production_local.py --docker /path/to/docker \
      --docker-host unix:///path/to/docker.sock --api-image autosound-production:qa \
      --postgres-image postgres:16-bookworm --mongo-image mongo:7.0 \
      --minio-image autosound-minio:local --output /path/to/empty-evidence-directory

Actual production APP_ENV/secrets/model/workers and pg_dump/mongodump are used.
All clients run inside the API container. Only the owned backup job's lifecycle
fields differ between its dump-time running state and its completed source state.
No claim is made of an atomic snapshot across independently written databases.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
import uuid


ROOT = Path(__file__).resolve().parents[1]
LABEL = "org.autosound.production-local-qa"

# This program is sent over Docker exec stdin, never through a public endpoint.
# Credentials stay in the disposable container/env-file and are not evidence.
INNER = r'''
import base64, difflib, gzip, hashlib, importlib.metadata, json, os, re, signal, socket, subprocess, sys, time, uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import bcrypt, boto3, httpx, psycopg2, psycopg2.extras
from psycopg2 import sql
from bson import BSON, Binary, Decimal128, Int64, ObjectId, Regex, json_util
from cryptography.fernet import Fernet
from pymongo import MongoClient

WORK = Path('/tmp/production-qa')
WORK.mkdir(mode=0o700, exist_ok=True)
REPORT = {'status':'running','scope':'Production Docker image; local internal network; real dumps/restores; SES disabled',
          'not_verified':['AWS deployment, IAM, billing, SES delivery or public TLS','CARLA','Atomic cross-database backup under concurrent writes'],
          'checks':[]}
apis=[]

class Failed(RuntimeError): pass
def ensure(ok, message):
    if not ok: raise Failed(message)
def passed(message):
    REPORT['checks'].append(message)
    print('PASS', message, flush=True)
def digest(value): return hashlib.sha256(value).hexdigest()
def canon(value): return json.dumps(value, sort_keys=True, separators=(',',':'),default=str).encode()
def tool(label, command, env=None):
    with (WORK/(label+'.log')).open('wb') as log:
        result=subprocess.run(command,env=env or os.environ,stdout=log,stderr=subprocess.STDOUT,timeout=600)
    ensure(result.returncode==0,label+' failed; inspect its local diagnostic log')
def wait(label, probe, timeout=120):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        try:
            if probe(): return
        except Exception: pass
        time.sleep(.5)
    raise Failed('Timed out waiting for '+label)
def http(port,method,path,body=None,token=None):
    headers={'Authorization':'Bearer '+token} if token else {}
    with httpx.Client(base_url=f'http://127.0.0.1:{port}',trust_env=False,follow_redirects=False,timeout=30) as c:
        r=c.request(method,path,json=body,headers=headers)
        try: data=r.json()
        except ValueError: data=None
        return r.status_code,data
def login(port,user):
    status,data=http(port,'POST','/login',{'email':user['email'],'password':user['password']})
    ensure(status==200 and data.get('access_token'),'Private restored/source login failed')
    return data['access_token']
def api_start(port, env, label):
    log=(WORK/(label+'.log')).open('wb')
    p=subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--host','0.0.0.0','--port',str(port),
                        '--workers','1','--no-proxy-headers','--no-access-log'],env=env,stdout=log,stderr=subprocess.STDOUT)
    apis.append((p,log))
    wait(label,lambda:p.poll() is None and http(port,'GET','/health')[0]==200)
    return p
def api_stop(p):
    p.terminate()
    try: p.wait(timeout=30)
    except subprocess.TimeoutExpired:
        p.kill(); p.wait(timeout=10)
    ensure(p.returncode in (0,-signal.SIGTERM),'API failed during quiescent shutdown')
def pg(database):
    return psycopg2.connect(host='postgres',port=5432,user='qa',password=os.environ['QA_PG_PASSWORD'],dbname=database,
                            connect_timeout=3,options='-c timezone=UTC')
def schema(database):
    env={**os.environ,'PGHOST':'postgres','PGPORT':'5432','PGUSER':'qa','PGPASSWORD':os.environ['QA_PG_PASSWORD'],'PGDATABASE':database}
    r=subprocess.run(['pg_dump','--schema-only','--no-owner','--no-privileges','-w'],env=env,capture_output=True,check=True)
    # Newer pg_dump emits a random psql restriction token; it is not database schema.
    return b'\n'.join(line for line in r.stdout.splitlines() if not line.startswith((b'\\restrict ',b'\\unrestrict ')))
def sql_snapshot(database, backup_id=None, omit_backup=False):
    result={'tables':{},'sequences':{},'schema_sha256':digest(schema(database))}
    with pg(database) as conn, conn.cursor() as cur:
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
        tables=[row[0] for row in cur.fetchall()]
        for table in tables:
            cur.execute(sql.SQL('SELECT row_to_json(t) FROM public.{} t').format(sql.Identifier(table)))
            rows=[row[0] for row in cur.fetchall()]
            if table=='maintenance_jobs' and backup_id:
                if omit_backup: rows=[row for row in rows if row['id']!=backup_id]
                else:
                    for row in rows:
                        if row['id']==backup_id:
                            for field in ('status','result','finished_at'): row[field]='<own-backup-lifecycle>'
            encoded=sorted(canon(row) for row in rows)
            result['tables'][table]={'count':len(rows),'rows_sha256':digest(b'\n'.join(encoded))}
        cur.execute("SELECT sequencename, start_value,min_value,max_value,increment_by,cycle,cache_size FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename")
        for row in cur.fetchall():
            cur.execute(sql.SQL('SELECT last_value,is_called FROM public.{}').format(sql.Identifier(row[0])))
            result['sequences'][row[0]]={'definition':list(row[1:]),'state':list(cur.fetchone())}
        cur.execute("SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE connamespace='public'::regnamespace ORDER BY conname")
        result['constraints']=cur.fetchall()
        cur.execute("SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='public' ORDER BY indexname")
        result['indexes']=cur.fetchall()
    return result
def mongo_snapshot(database):
    out={}
    for name in sorted(database.list_collection_names()):
        collection=database[name]
        # Encode the actual BSON, not lossy JSON, so Binary/Decimal128/Int64/dates/field order survive.
        documents=sorted(BSON.encode(row) for row in collection.find())
        indexes=[]
        for row in collection.list_indexes():
            item=dict(row); item.pop('ns',None)
            item['key']=list(item['key'].items())
            indexes.append(item)
        indexes.sort(key=lambda row:row['name'])
        out[name]={'count':len(documents),'bson_sha256':digest(b'\n'.join(documents)),
                   'indexes':json.loads(json_util.dumps(indexes,json_options=json_util.CANONICAL_JSON_OPTIONS)),
                   'options':json.loads(json_util.dumps(collection.options(),json_options=json_util.CANONICAL_JSON_OPTIONS))}
    return out
def component_check(port,label,bundle):
    env=dict(os.environ)
    for role,name in [('iot','iot_staff'),('cloud','cloud_staff')]:
        user=next(row for row in bundle['users'] if row['role']==name)
        env['VERIFY_'+role.upper()+'_EMAIL']=user['email']; env['VERIFY_'+role.upper()+'_PASSWORD']=user['password']
    target=WORK/(label+'.json')
    tool(label,[sys.executable,str(WORK/'verify_components.py'),'--base-url',f'http://127.0.0.1:{port}',
                '--output',str(target)],env)
    result=json.loads(target.read_text())
    ensure(result.get('result')=='PASS','Real component verifier did not pass')
    return result

try:
    ensure(os.environ['APP_ENV']=='production' and os.environ['DEV_FAKE_BACKUP']=='false'
           and os.environ['DEV_FAKE_EMAIL']=='true','Unsafe production/local side-effect configuration')
    ensure(not Path('/app/backend/.env').exists(),'Image unexpectedly contains a real .env file')
    routes=Path('/proc/net/route').read_text().splitlines()[1:]
    ensure(not any(row.split()[1]=='00000000' and int(row.split()[3],16)&1 for row in routes),'Internal Docker container has a default IPv4 route')
    REPORT['network']={'default_ipv4_route':False,'external_dns_forwarding':'disabled via Docker --dns=127.0.0.1',
                       'ports_published':False,'internal_network':True}
    versions={name:importlib.metadata.version(name) for name in ('torch','torchaudio','fastapi','librosa','pymongo','boto3','sqlalchemy')}
    ensure(sys.version_info[:2]==(3,12),'Production image must use Python 3.12')
    ensure(versions['torch'].startswith('2.5.1') and versions['torchaudio'].startswith('2.5.1'),'Production image model stack differs from its pinned versions')
    REPORT['python']=sys.version.split()[0]; REPORT['dependencies']=versions
    REPORT['tools']={name:subprocess.check_output([name,'--version'],text=True).splitlines()[0]
                     for name in ('pg_dump','pg_restore','mongodump','mongorestore')}
    ensure('PostgreSQL) 16.' in REPORT['tools']['pg_dump'],'Production backup requires pg_dump 16')
    wait('PostgreSQL',lambda:pg('postgres').close() is None)
    mongo=MongoClient(os.environ['MONGO_URI'],serverSelectionTimeoutMS=1000,tz_aware=True)
    wait('MongoDB',lambda:mongo.admin.command('ping')['ok']==1)
    source_mongo=mongo.get_default_database()
    from app.db import s3
    from app.config import get_settings
    client=s3.get_client(); bucket=get_settings().S3_BUCKET
    wait('MinIO',lambda:client.list_buckets() is not None)
    client.create_bucket(Bucket=bucket,CreateBucketConfiguration={'LocationConstraint':'us-west-2'})
    tool('migrations',[sys.executable,'-m','alembic','upgrade','head'])
    bundle_path=WORK/'bootstrap.json'
    tool('private-bootstrap',[sys.executable,'-m','app.db.bootstrap','--initialize-empty-database','--credentials-file',str(bundle_path)])
    bundle=json.loads(bundle_path.read_text())
    ensure(len(bundle['users'])==4 and len(bundle['devices'])==5 and bundle_path.stat().st_mode&0o777==0o600,
           'Private production bootstrap counts or bundle permissions differ')
    source_api=api_start(8000,dict(os.environ),'api-source')
    cloud_user=next(row for row in bundle['users'] if row['role']=='cloud_staff')
    cloud=login(8000,cloud_user)
    wait('production readiness',lambda:http(8000,'GET','/admin/readiness',token=cloud)[0]==200)
    ready=http(8000,'GET','/admin/readiness',token=cloud)[1]
    ensure(ready['scope']['side_effect_modes']=={'email':'logged','backup':'real'},'Server did not confirm real backups and disabled email')
    REPORT['source_readiness']=ready
    if os.environ.get('QA_FRONTEND_ENABLED')=='true':
        with httpx.Client(base_url='http://frontend:8080',trust_env=False,follow_redirects=False,timeout=30) as web:
            wait('production nginx',lambda:web.get('/healthz').status_code==200)
            index=web.get('/')
            ensure(index.status_code==200 and 'id="root"' in index.text,'Production frontend index is missing')
            assets=re.findall(r'(?:src|href)="(/assets/[^" ]+)"',index.text)
            ensure(assets and any(path.endswith('.js') for path in assets),'Built frontend assets are missing')
            for path in assets:
                asset=web.get(path)
                ensure(asset.status_code==200 and 'immutable' in asset.headers.get('cache-control',''),
                       'Built production asset is not served with its intended cache policy')
            ensure(web.get('/devices?tab=data').status_code==200,'Production SPA route fallback failed')
            ensure(web.get('/api/health').status_code==200,'nginx API health proxy failed')
            proxied=web.post('/api/login',json={'email':cloud_user['email'],'password':cloud_user['password']})
            ensure(proxied.status_code==200,'Production nginx private login proxy failed')
            headers={'Authorization':'Bearer '+proxied.json()['access_token']}
            ensure(web.get('/api/me',headers=headers).status_code==200,'Production nginx JWT profile proxy failed')
            ensure(web.get('/api/admin/readiness').status_code==401,'Production nginx exposed anonymous readiness')
            ensure(web.get('/api/admin/readiness',headers=headers).status_code==200,'Production nginx readiness proxy failed')
        REPORT['frontend']={'status':'passed','built_assets':assets,'spa_fallback':True,'api_health':200,
                             'private_login_and_profile':True,'anonymous_readiness':401,'authenticated_readiness':200}
        passed('Built production nginx serves static assets/SPA fallback and proxies real health/private JWT/readiness')
    original_component=component_check(8000,'components-source',bundle)
    REPORT['components_source']=original_component
    # Explicit rich sentinel rows plus sequence gaps exercise more than row counts.
    wifi_password='qa-'+uuid.uuid4().hex
    sentinel_key='qa-'+uuid.uuid4().hex
    encrypted=Fernet(os.environ['WIFI_ENCRYPTION_KEY'].encode()).encrypt(wifi_password.encode())
    sentinel_job=str(uuid.uuid4())
    with pg('autosound_source') as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO subscriptions(owner_id,car_id,alert_type,enabled,notify_method) VALUES(1,1,'human',true,'in_app')")
        cur.execute("INSERT INTO devices(car_id,name,device_type,status,wifi_ssid,wifi_security,wifi_password_enc,ip_mode,device_key_hash,firmware) VALUES(1,'qa-restore-sentinel','microphone','registered','qa-restore','wpa3',%s,'dhcp',%s,'qa-restore') RETURNING id",(encrypted,hashlib.sha256(sentinel_key.encode()).hexdigest()))
        sentinel_device=cur.fetchone()[0]
        cur.execute("INSERT INTO device_commands(device_id,command,payload,status,expires_at,acked_at) VALUES(%s,'sync_config',%s,'acknowledged',now()+interval '1 day',now())",(sentinel_device,psycopg2.extras.Json({'qa':{'nested':[1,True,None]}})))
        cur.execute("INSERT INTO maintenance_jobs(id,job_type,status,params,result,started_at,finished_at,created_by) VALUES(%s,'retention','succeeded',%s,%s,now(),now(),4)",(sentinel_job,psycopg2.extras.Json({'qa':True,'nested':{'array':[1,None,'sentinel']}}),psycopg2.extras.Json({'deleted':0})))
        cur.execute("SELECT setval(pg_get_serial_sequence('devices','id'),1000,false)")
    coll=source_mongo.create_collection('qa_backup_sentinel',validator={'ordinal':{'$type':'int'}})
    coll.create_index([('tenant',1),('ordinal',-1)],unique=True,name='tenant_ordinal_unique')
    coll.create_index('optional',sparse=True,name='optional_sparse')
    coll.insert_many([{'_id':ObjectId(),'tenant':'qa','ordinal':i,'large':Int64(2**50+i),
                       'decimal':Decimal128('1234.5678'),'binary':Binary(b'\x00\xffqa',128),
                       'uuid':Binary.from_uuid(uuid.uuid4()),'at':datetime.now(timezone.utc),
                       'pattern':Regex('^qa.*','i'),'nested':{'values':[i,True,None,'unicode-☃']}}
                      for i in range(3)])
    source_mongo.create_collection('qa_empty_collection')
    source_sql_before=sql_snapshot('autosound_source')
    source_mongo_before=mongo_snapshot(source_mongo)
    source_objects={item['Key']:digest(client.get_object(Bucket=bucket,Key=item['Key'])['Body'].read())
                    for page in client.get_paginator('list_objects_v2').paginate(Bucket=bucket)
                    for item in page.get('Contents',[])}
    passed('Production Python 3.12/Torch 2.5.1, private bootstrap, real workers/model and source C2/C3 inference')
    status,queued=http(8000,'POST','/admin/db/backup',token=cloud)
    ensure(status==202,'Real backup HTTP request was not accepted')
    job_id=queued['job_id']
    def backup_done():
        status,rows=http(8000,'GET','/admin/db/backups',token=cloud)
        ensure(status==200,'Backup status lookup failed')
        job=next(row for row in rows if row['job_id']==job_id)
        if job['status']=='failed': raise Failed('Real backup job failed')
        return job['status']=='succeeded'
    # Poll explicitly so a failed job is not mistaken for a transient readiness exception.
    end=time.monotonic()+180
    while not backup_done():
        ensure(time.monotonic()<end,'Real backup job timed out'); time.sleep(.5)
    # No other writer is used in this test. Stop the source API immediately after the job so
    # sweeper/worker lifecycles cannot change source state during restore comparisons.
    api_stop(source_api)
    with pg('autosound_source') as conn, conn.cursor() as cur:
        cur.execute('SELECT status,params,result FROM maintenance_jobs WHERE id=%s',(job_id,))
        job_status,job_params,job_result=cur.fetchone()
    ensure(job_status=='succeeded' and job_params['dev_fake'] is False,'Backup result is not an actual dump')
    source_sql=sql_snapshot('autosound_source',job_id)
    ensure(sql_snapshot('autosound_source',job_id,True)==source_sql_before,'Backup unexpectedly changed other source SQL rows/schema/sequences')
    ensure(mongo_snapshot(source_mongo)==source_mongo_before,'Backup unexpectedly changed source MongoDB')
    pg_key,mg_key=job_result['postgres_key'],job_result['mongo_key']
    archives={}
    for kind,key,name in [('postgres',pg_key,'postgres.dump'),('mongo',mg_key,'mongo.archive.gz')]:
        raw=client.get_object(Bucket=bucket,Key=key)['Body'].read()
        (WORK/name).write_bytes(raw)
        ensure(client.get_object(Bucket=bucket,Key=key)['Body'].read()==raw,'Repeated local backup download differed')
        archives[kind]={'object_key':key,'bytes':len(raw),'sha256':digest(raw)}
    pg_raw=(WORK/'postgres.dump').read_bytes()
    mg_raw=(WORK/'mongo.archive.gz').read_bytes()
    ensure(pg_raw.startswith(b'PGDMP') and pg_raw!=b'PGDMP fake dev backup\n','PostgreSQL archive is not a genuine custom dump')
    decoded=gzip.decompress(mg_raw)
    ensure(decoded and decoded!=b'fake dev mongodump archive\n','Mongo archive is not a genuine dump')
    tool('pg-archive-list',['pg_restore','--list',str(WORK/'postgres.dump')])
    REPORT['backup']={'job_id':job_id,'dev_fake':False,'archives':archives,
                      'consistency':'Quiescent application data; sequential PG/Mongo dumps, not an atomic cross-store snapshot'}
    passed('HTTP real backup succeeded; genuine pg_restore-readable custom archive and gzip Mongo archive downloaded with stable SHA-256')
    admin=pg('postgres')
    admin.autocommit=True
    try:
        with admin.cursor() as cur:
            cur.execute('CREATE DATABASE autosound_restored OWNER qa')
            cur.execute('CREATE DATABASE autosound_schema_reference OWNER qa')
    finally:
        admin.close()
    restore_pg_env={**os.environ,'PGHOST':'postgres','PGPORT':'5432','PGUSER':'qa',
                    'PGPASSWORD':os.environ['QA_PG_PASSWORD'],'PGDATABASE':'autosound_restored'}
    tool('postgres-restore',['pg_restore','--exit-on-error','--no-owner','--no-privileges','--dbname=autosound_restored',
                            '-w',str(WORK/'postgres.dump')],restore_pg_env)
    # PostgreSQL can deparse an array-wide varchar->text CHECK cast differently after parsing
    # pg_dump output. Independently load the live source's COMPLETE schema-only SQL into a
    # third fresh DB. Compare that re-parsed catalog, without deleting/ignoring any constraint.
    schema_file=WORK/'source-schema.sql'
    tool('schema-reference-dump',['pg_dump','--schema-only','--no-owner','--no-privileges','-w',
                                  '--file='+str(schema_file)],{**restore_pg_env,'PGDATABASE':'autosound_source'})
    tool('schema-reference-load',['psql','--no-psqlrc','--set=ON_ERROR_STOP=1','-w','--file='+str(schema_file)],
                                 {**restore_pg_env,'PGDATABASE':'autosound_schema_reference'})
    reference_sql=sql_snapshot('autosound_schema_reference')
    mongo_config=WORK/'mongorestore.json'
    descriptor=os.open(mongo_config,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(descriptor,'w') as stream: json.dump({'uri':os.environ['QA_MONGO_ADMIN_URI']},stream)
    try:
        tool('mongo-restore',['mongorestore','--config='+str(mongo_config),'--archive='+str(WORK/'mongo.archive.gz'),
                              '--gzip','--nsInclude=autosound_source.*','--nsFrom=autosound_source.*',
                              '--nsTo=autosound_restored.*','--stopOnError'])
    finally: mongo_config.unlink(missing_ok=True)
    restored_sql=sql_snapshot('autosound_restored',job_id)
    restored_mongo=mongo_snapshot(mongo['autosound_restored'])
    expected_sql={**source_sql,'schema_sha256':reference_sql['schema_sha256'],
                  'constraints':reference_sql['constraints']}
    REPORT['schema_normalization']={
        'method':'Full source schema-only pg_dump SQL independently loaded with psql ON_ERROR_STOP into a third fresh reference database',
        'reason':'PostgreSQL reparses array-wide varchar-to-text CHECK casts into equivalent per-element casts',
        'raw_source_schema_sha256':source_sql['schema_sha256'],
        'reference_schema_sha256':reference_sql['schema_sha256'],
        'restored_schema_sha256':restored_sql['schema_sha256'],
        'constraints_compared':'Complete independent reference catalog; none omitted or regex-stripped',
        'raw_schema_unified_diff':list(difflib.unified_diff(schema('autosound_source').decode().splitlines(),
            schema('autosound_restored').decode().splitlines(),fromfile='raw-source-schema',tofile='restored-schema'))}
    # Save safe diagnostics BEFORE assertions, so a restore mismatch remains reviewable after
    # container cleanup. These contain only schema/index definitions, row hashes and counts.
    REPORT['sql_restore']={'status':'comparing','source':source_sql,'restored':restored_sql,
                           'expected_after_independent_schema_reparse':expected_sql}
    if restored_sql!=expected_sql:
        REPORT['sql_difference']={
            'tables':{name:{'source':source_sql['tables'].get(name),'restored':restored_sql['tables'].get(name)}
                      for name in sorted(set(source_sql['tables'])|set(restored_sql['tables']))
                      if source_sql['tables'].get(name)!=restored_sql['tables'].get(name)},
            'sequences':{name:{'source':source_sql['sequences'].get(name),'restored':restored_sql['sequences'].get(name)}
                         for name in sorted(set(source_sql['sequences'])|set(restored_sql['sequences']))
                         if source_sql['sequences'].get(name)!=restored_sql['sequences'].get(name)},
            'constraints_equal':expected_sql['constraints']==restored_sql['constraints'],
            'indexes_equal':source_sql['indexes']==restored_sql['indexes'],
            'schema_unified_diff':list(difflib.unified_diff(schema('autosound_schema_reference').decode().splitlines(),
                 schema('autosound_restored').decode().splitlines(),fromfile='reference-schema',tofile='restored-schema'))}
    REPORT['mongo_restore']={'status':'comparing','source':source_mongo_before,'restored':restored_mongo}
    ensure(restored_sql==expected_sql,'Restored SQL schema/rows/constraints/indexes/sequences differ')
    ensure(restored_mongo==source_mongo_before,'Restored Mongo BSON/documents/indexes/collection options differ')
    with pg('autosound_restored') as conn, conn.cursor() as cur:
        cur.execute('SELECT status,result,finished_at FROM maintenance_jobs WHERE id=%s',(job_id,))
        ensure(cur.fetchone()==('running',None,None),'Dump-time job lifecycle differs from the documented exception')
        cur.execute('SELECT wifi_password_enc,device_key_hash FROM devices WHERE id=%s',(sentinel_device,))
        encrypted_restored,key_hash=cur.fetchone()
        ensure(Fernet(os.environ['WIFI_ENCRYPTION_KEY'].encode()).decrypt(bytes(encrypted_restored)).decode()==wifi_password,
               'Restored encrypted Wi-Fi bytes cannot be decrypted with the retained key')
        ensure(key_hash==hashlib.sha256(sentinel_key.encode()).hexdigest(),'Private sentinel device key hash differs')
    # Verify actual restored constraints, then roll back each deliberately invalid insert.
    with pg('autosound_restored') as conn:
        try:
            with conn.cursor() as cur: cur.execute("INSERT INTO cars(id,owner_id,make,model) VALUES(-77,-77,'qa','bad')")
        except psycopg2.errors.ForeignKeyViolation: conn.rollback()
        else: raise Failed('Restored foreign key was not enforced')
        try:
            with conn.cursor() as cur: cur.execute("INSERT INTO users(id,name,email,role,password_hash) SELECT -77,name,email,role,password_hash FROM users LIMIT 1")
        except psycopg2.errors.UniqueViolation: conn.rollback()
        else: raise Failed('Restored unique index was not enforced')
    REPORT['sql_restore']={'status':'passed','source':source_sql,'restored':restored_sql,
                           'expected_after_independent_schema_reparse':expected_sql,
                           'own_job_normalized_fields':['status','result','finished_at'],
                           'own_job_dump_status':'running','source_completed_job_status':'succeeded',
                           'actual_fk_and_unique_rejections':True,'encrypted_wifi_and_key_hash_preserved':True}
    REPORT['mongo_restore']={'status':'passed','source':source_mongo_before,'restored':restored_mongo,
                             'comparison':'All collections; exact BSON bytes per document, counts, ordered index keys and collection options'}
    passed('Actual pg_restore/mongorestore matched all SQL tables/schema/FKs/indexes/sequence state and all Mongo collections/BSON/indexes, including rich/empty sentinels')
    restored_env={**os.environ,'DATABASE_URL':os.environ['DATABASE_URL'].replace('/autosound_source','/autosound_restored'),
                  'MONGO_URI':os.environ['MONGO_URI'].replace('/autosound_source','/autosound_restored')}
    restored_api=api_start(8001,restored_env,'api-restored')
    restored_cloud=login(8001,cloud_user)
    wait('restored model/workers readiness',lambda:http(8001,'GET','/admin/readiness',token=restored_cloud)[0]==200)
    for user in bundle['users']: login(8001,user)
    mic=next(row for row in bundle['devices'] if row['name']=='mic-1')
    with httpx.Client(base_url='http://127.0.0.1:8001',trust_env=False,timeout=30) as c:
        ensure(c.post(f"/devices/{mic['id']}/heartbeat",headers={'X-Device-Key':mic['device_key']},json={}).status_code==200,
               'Restored private device key no longer authenticates')
        ensure(c.post(f"/devices/{mic['id']}/heartbeat",headers={'X-Device-Key':'invalid'},json={}).status_code==401,
               'Invalid restored device key was accepted')
    REPORT['components_restored']=component_check(8001,'components-restored',bundle)
    ensure(sql_snapshot('autosound_source',job_id)==source_sql,'Restored API/inference changed original source SQL')
    ensure(mongo_snapshot(source_mongo)==source_mongo_before,'Restored API/inference changed original source MongoDB')
    for key,sha in source_objects.items():
        ensure(digest(client.get_object(Bucket=bucket,Key=key)['Body'].read())==sha,'Restored API/inference changed an original source S3 object')
    for kind,key,name in [('postgres',pg_key,'postgres.dump'),('mongo',mg_key,'mongo.archive.gz')]:
        ensure(digest(client.get_object(Bucket=bucket,Key=key)['Body'].read())==archives[kind]['sha256'],'Backup object changed during restore')
    REPORT['original_source_preserved']=True
    REPORT['original_s3_objects']={'count':len(source_objects),'sha256_by_key':source_objects}
    REPORT['restored_access']={'private_logins':4,'original_private_device_key':True,'active_model_loaded':True,
                               'real_processing_and_signed_audio_download':True}
    passed('All private restored logins/key and real deployed-model C2/C3 inference passed; original source and backup objects preserved')
    REPORT['status']='passed'
except BaseException as exc:
    REPORT['status']='failed'
    REPORT['failure']=str(exc) if isinstance(exc,Failed) else type(exc).__name__
    print('Production local QA failed:',REPORT['failure'],flush=True)
finally:
    for process,log in reversed(apis):
        if process.poll() is None:
            process.terminate()
            try: process.wait(timeout=20)
            except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=10)
        log.close()
    (WORK/'verification.json').write_text(json.dumps(REPORT,indent=2,default=str)+'\n')
sys.exit(0 if REPORT['status']=='passed' else 1)
'''


class Failed(RuntimeError):
    pass


def ensure(ok, message):
    if not ok:
        raise Failed(message)


def main():
    parser=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--docker',type=Path,required=True,help='existing Docker CLI executable or wrapper')
    parser.add_argument('--docker-host',required=True,help='explicit local unix:///.../docker.sock')
    parser.add_argument('--api-image',required=True,help='prebuilt image with INSTALL_BACKUP_TOOLS=true')
    parser.add_argument('--postgres-image',default='postgres:16-bookworm')
    parser.add_argument('--mongo-image',default='mongo:7.0')
    parser.add_argument('--minio-image',required=True)
    parser.add_argument('--frontend-image',help='optional prebuilt production nginx frontend image')
    parser.add_argument('--output',type=Path,required=True,help='new/empty evidence directory')
    args=parser.parse_args()
    os.umask(0o077)
    output=args.output.absolute()
    output.mkdir(parents=True,exist_ok=True)
    ensure(not any(output.iterdir()),'Output directory must be empty')
    endpoint=urlsplit(args.docker_host)
    ensure(endpoint.scheme=='unix' and not endpoint.netloc and not endpoint.query and not endpoint.fragment,
           'Only an explicit local unix Docker socket is allowed')
    ensure(stat.S_ISSOCK(Path(endpoint.path).stat().st_mode),'Docker socket is not a local Unix socket')
    ensure(args.docker.is_file() and os.access(args.docker,os.X_OK),'Docker executable is missing')
    run_id=uuid.uuid4().hex[:12]
    prefix='autosound-prod-qa-'+run_id
    network=prefix+'-net'
    owned=[]
    api=None
    network_created=False
    runtime=Path(tempfile.mkdtemp(prefix='autosound-production-qa-'))
    (runtime/'docker-config').mkdir()
    env={key:os.environ[key] for key in ('PATH','HOME','XDG_RUNTIME_DIR') if key in os.environ}
    env['DOCKER_CONFIG']=str(runtime/'docker-config')
    report={'status':'running','started_at':datetime.now(timezone.utc).isoformat(),
            'scope':'Local prebuilt production Docker image; no pulls/builds/AWS/SES/CARLA',
            'run_label':{LABEL:run_id},'images':{},'cleanup':{}}
    secret_values=[]

    def redact(text):
        for value in secret_values:
            text=text.replace(value,'<local-qa-secret>')
        text=re.sub(r'eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}','<jwt>',text)
        return re.sub(r'(X-Amz-Signature=)[0-9a-fA-F]{64}',r'\1<signature>',text)

    def docker(*command,input=None,timeout=120,expected=0,log=None):
        result=subprocess.run([str(args.docker),'--host',args.docker_host,*map(str,command)],env=env,
                              input=input,capture_output=True,timeout=timeout)
        if log:
            (output/log).write_text(redact((result.stdout+result.stderr).decode(errors='replace')))
        ensure(result.returncode==expected,'Docker '+str(command[0])+' failed; see diagnostic logs')
        return result.stdout

    def container(name,image,environment,command,alias):
        target=prefix+'-'+name
        envfile=runtime/(name+'.env')
        envfile.write_text(''.join(key+'='+value+'\n' for key,value in environment.items()))
        envfile.chmod(0o600)
        docker('create','--pull=never','--name',target,'--label',LABEL+'='+run_id,
               '--network',network,'--network-alias',alias,'--dns','127.0.0.1','--env-file',envfile,
               '--entrypoint',command[0],image,*command[1:],log=name+'-create.log')
        owned.append(target)
        docker('start',target,log=name+'-start.log')
        return target

    code=1
    try:
        for name,image in (('api',args.api_image),('postgres',args.postgres_image),('mongo',args.mongo_image),('minio',args.minio_image)):
            info=json.loads(docker('image','inspect',image,'--format','{{json .}}'))
            report['images'][name]={key:info.get(key) for key in ('Id','RepoTags','Architecture','Os')}
        if args.frontend_image:
            info=json.loads(docker('image','inspect',args.frontend_image,'--format','{{json .}}'))
            report['images']['frontend']={key:info.get(key) for key in ('Id','RepoTags','Architecture','Os')}
        docker('network','create','--internal','--label',LABEL+'='+run_id,network,log='network-create.log')
        network_created=True
        internal=docker('network','inspect',network,'--format','{{json .Internal}}').decode().strip()
        ensure(internal=='true','Docker network is not internal')
        pg_password=secrets.token_hex(24)
        mongo_password=secrets.token_hex(24)
        access='qa'+secrets.token_hex(8)
        secret=secrets.token_urlsafe(36)
        jwt_secret=secrets.token_urlsafe(48)
        wifi_key=base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
        secret_values.extend([pg_password,mongo_password,access,secret,jwt_secret,wifi_key])
        pg_environment={'POSTGRES_USER':'qa','POSTGRES_PASSWORD':pg_password,'POSTGRES_DB':'autosound_source'}
        mongo_environment={'MONGO_INITDB_ROOT_USERNAME':'qa','MONGO_INITDB_ROOT_PASSWORD':mongo_password}
        minio_environment={'MINIO_ROOT_USER':access,'MINIO_ROOT_PASSWORD':secret,'MINIO_UPDATE':'off','MINIO_BROWSER':'off'}
        container('postgres',args.postgres_image,pg_environment,['docker-entrypoint.sh','postgres'],'postgres')
        container('mongo',args.mongo_image,mongo_environment,['docker-entrypoint.sh','mongod','--bind_ip_all'],'mongo')
        container('minio',args.minio_image,minio_environment,['minio','server','/data','--address',':9000'],'minio')
        mongo_admin=f'mongodb://qa:{mongo_password}@mongo:27017/?authSource=admin'
        api_environment={
            'APP_ENV':'production','DATABASE_URL':f'postgresql+psycopg2://qa:{pg_password}@postgres:5432/autosound_source',
            'MONGO_URI':f'mongodb://qa:{mongo_password}@mongo:27017/autosound_source?authSource=admin',
            'S3_BUCKET':'autosound-production-local-qa','S3_ENDPOINT_URL':'http://minio:9000',
            'S3_PUBLIC_ENDPOINT_URL':'http://minio:9000','AWS_REGION':'us-west-2','AWS_DEFAULT_REGION':'us-west-2',
            'AWS_ACCESS_KEY_ID':access,'AWS_SECRET_ACCESS_KEY':secret,'AWS_EC2_METADATA_DISABLED':'true',
            'AWS_CONFIG_FILE':'/dev/null','AWS_SHARED_CREDENTIALS_FILE':'/dev/null',
            'JWT_SECRET':jwt_secret,'WIFI_ENCRYPTION_KEY':wifi_key,'CORS_ORIGINS':'https://qa.example.invalid',
            'DEV_FAKE_BACKUP':'false','DEV_FAKE_EMAIL':'true','SES_FROM_EMAIL':'qa@example.invalid',
            'PROCESSING_WORKER_ENABLED':'true','DEVICE_SWEEPER_ENABLED':'true','PROCESSING_WORKER_POLL_SECONDS':'1',
            'MONGO_ENSURE_INDEXES_ON_STARTUP':'true','LOAD_MODEL_ON_STARTUP':'true','FAIL_INTERRUPTED_JOBS_ON_STARTUP':'true',
            'MODEL_DIR':'/app/model/artifacts','LOCAL_AUDIO_DIR':'','QA_PG_PASSWORD':pg_password,
            'QA_MONGO_ADMIN_URI':mongo_admin,'OMP_NUM_THREADS':'1','MKL_NUM_THREADS':'1',
            'QA_FRONTEND_ENABLED':'true' if args.frontend_image else 'false',
        }
        api=container('api',args.api_image,api_environment,['python','-c','import time; time.sleep(86400)'],'api')
        docker('exec',api,'python','-c',"from pathlib import Path; Path('/tmp/production-qa').mkdir(mode=0o700,exist_ok=True)")
        docker('exec','-i',api,'python','-c',
               "import sys; from pathlib import Path; Path('/tmp/production-qa/verify_components.py').write_bytes(sys.stdin.buffer.read())",
               input=(ROOT/'scripts'/'verify_components.py').read_bytes())
        if args.frontend_image:
            container('frontend',args.frontend_image,{},['nginx','-g','daemon off;'],'frontend')
        result=subprocess.run([str(args.docker),'--host',args.docker_host,'exec','-i',api,'python','-'],
                              env=env,input=INNER.encode(),capture_output=True,timeout=1800)
        (output/'production-checks.log').write_text(redact((result.stdout+result.stderr).decode(errors='replace')))
        docker('cp',api+':/tmp/production-qa/verification.json',runtime/'inner-verification.json')
        report['production_checks']=json.loads((runtime/'inner-verification.json').read_text())
        for filename in ('components-source.json','components-restored.json'):
            copied=subprocess.run([str(args.docker),'--host',args.docker_host,'cp',api+':/tmp/production-qa/'+filename,str(runtime/filename)],env=env,capture_output=True)
            if copied.returncode==0:
                (output/filename).write_text(redact((runtime/filename).read_text()))
        ensure(result.returncode==0 and report['production_checks']['status']=='passed','Production backup/restore checks failed; see production-checks.log')
        report['status']='passed'
        code=0
    except BaseException as exc:
        report['status']='failed'
        report['failure']=str(exc) if isinstance(exc,Failed) else type(exc).__name__
        print('Production local QA failed: '+report['failure'],file=sys.stderr)
    finally:
        failures=[]
        for target in reversed(owned):
            try:
                label=docker('inspect',target,'--format','{{index .Config.Labels "'+LABEL+'"}}').decode().strip()
                ensure(label==run_id,'Refusing to remove a container without this run label')
                logs=subprocess.run([str(args.docker),'--host',args.docker_host,'logs',target],env=env,capture_output=True,timeout=30)
                (output/(target.rsplit('-',1)[-1]+'-container.log')).write_text(redact((logs.stdout+logs.stderr).decode(errors='replace')))
                if target==api:
                    try:
                        raw=docker('exec',api,'python','-c',
                            "import json; from pathlib import Path; print(json.dumps({p.name:p.read_text(errors='replace')[-2000000:] for p in Path('/tmp/production-qa').glob('*.log')}))")
                        (output/'logs').mkdir(exist_ok=True)
                        for name,text in json.loads(raw).items():
                            (output/'logs'/Path(name).name).write_text(redact(text))
                    except Exception as exc:
                        report['diagnostic_collection_error']=type(exc).__name__
                docker('rm','--force','--volumes',target,log=target.rsplit('-',1)[-1]+'-cleanup.log')
            except BaseException as exc:
                failures.append({'resource':target,'error_type':type(exc).__name__})
        if network_created:
            try:
                label=docker('network','inspect',network,'--format','{{index .Labels "'+LABEL+'"}}').decode().strip()
                ensure(label==run_id,'Refusing to remove a network without this run label')
                docker('network','rm',network,log='network-cleanup.log')
            except BaseException as exc:
                failures.append({'resource':network,'error_type':type(exc).__name__})
        report['cleanup']={'owned_containers_and_anonymous_volumes_removed':not failures,
                           'owned_internal_network_removed':network_created and not failures,'errors':failures,
                           'existing_resources_pruned':False}
        if failures: report['status'],code='failed',1
        shutil.rmtree(runtime)
        report['finished_at']=datetime.now(timezone.utc).isoformat()
        (output/'verification.json').write_text(redact(json.dumps(report,indent=2,default=str)+'\n'))
        print('Production local QA '+report['status']+'; evidence: '+str(output/'verification.json'))
    return code


if __name__=='__main__':
    try:
        sys.exit(main())
    except (Failed,OSError) as exc:
        print(str(exc) if isinstance(exc,Failed) else type(exc).__name__,file=sys.stderr)
        sys.exit(2)
