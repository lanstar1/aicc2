#!/usr/bin/env python3
"""Resumable SFTP push - skip files already uploaded (via progress file)."""
import os, sys, time, json
from pathlib import Path
import paramiko

HERE = Path(__file__).parent
MERGED_DIR = HERE / "products_with_features"
PUSH_LOG   = HERE / "push_progress.json"

HOST = "${GODOMALL_SFTP_HOST}"
PORT = 17662
USER = "${GODOMALL_SFTP_USER}"
PASSWORD = "${GODOMALL_SFTP_PASSWORD}"
REMOTE = "aicc2/products"

MAX_SECONDS = int(sys.argv[1]) if len(sys.argv) > 1 else 40

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, port=PORT, username=USER, password=PASSWORD)
sftp = ssh.open_sftp()

done = set()
if PUSH_LOG.exists():
    done = set(json.loads(PUSH_LOG.read_text()))

files = sorted(MERGED_DIR.glob("*.json"))
todo = [f for f in files if f.name not in done]
print(f"Total={len(files)}, already pushed={len(done)}, todo={len(todo)}")

start = time.time()
pushed = 0
for f in todo:
    if time.time() - start > MAX_SECONDS:
        print(f"  [time] stopping at {pushed} pushed this run")
        break
    try:
        sftp.put(str(f), f"{REMOTE}/{f.name}")
        done.add(f.name)
        pushed += 1
        if pushed % 50 == 0:
            PUSH_LOG.write_text(json.dumps(sorted(done)))
            print(f"  [{len(done)}/{len(files)}] elapsed={time.time()-start:.1f}s", flush=True)
    except Exception as e:
        print(f"  [fail] {f.name}: {e}")

PUSH_LOG.write_text(json.dumps(sorted(done)))
print(f"Pushed this run: {pushed}, Total done: {len(done)}/{len(files)}")
sftp.close(); ssh.close()
