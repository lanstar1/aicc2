#!/usr/bin/env python3
"""
Phase 2용 SFTP 동기화 유틸.

모드:
  --pull         서버의 aicc2/products/*.json 전체를 로컬 raw_products/ 로 내려받는다.
                 (백업 겸 features 병합 전 원본 확보)
  --push         로컬 products_with_features/*.json 을 서버 aicc2/products/ 로 올린다.
                 (features 병합된 최종본으로 덮어쓰기)
  --list         서버 products/ 개수 및 용량 요약.

실행:
  python sftp_sync.py --pull
  python sftp_sync.py --push --limit 5   # 시험용
  python sftp_sync.py --push             # 전체 업로드
"""
import os, sys, argparse
from pathlib import Path
import paramiko

HERE = Path(__file__).parent
RAW_DIR      = HERE / "raw_products"
MERGED_DIR   = HERE / "products_with_features"

HOST = "${GODOMALL_SFTP_HOST}"
PORT = 17662
USER = "${GODOMALL_SFTP_USER}"
PASSWORD = "${GODOMALL_SFTP_PASSWORD}"
REMOTE = "aicc2/products"

def connect():
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, port=PORT, username=USER, password=PASSWORD)
    return ssh, ssh.open_sftp()

def cmd_list(sftp):
    files = sftp.listdir(REMOTE)
    total = 0
    for f in files:
        try:
            total += sftp.stat(f"{REMOTE}/{f}").st_size
        except Exception:
            pass
    print(f"Remote products/: {len(files)} files, {total/1024/1024:.1f} MB")

def cmd_pull(sftp):
    RAW_DIR.mkdir(exist_ok=True)
    files = sorted(sftp.listdir(REMOTE))
    print(f"Pulling {len(files)} files → {RAW_DIR}")
    for i, f in enumerate(files, 1):
        dest = RAW_DIR / f
        if dest.exists():
            continue
        sftp.get(f"{REMOTE}/{f}", str(dest))
        if i % 50 == 0:
            print(f"  [{i}/{len(files)}]", flush=True)
    print(f"Done. Local: {len(list(RAW_DIR.glob('*.json')))} files")

def cmd_push(sftp, limit=0):
    files = sorted(MERGED_DIR.glob("*.json"))
    if limit:
        files = files[:limit]
    print(f"Pushing {len(files)} files → {REMOTE}/")
    for i, f in enumerate(files, 1):
        sftp.put(str(f), f"{REMOTE}/{f.name}")
        if i % 25 == 0:
            print(f"  [{i}/{len(files)}]", flush=True)
    print("Done.")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pull", action="store_true")
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    if not (args.pull or args.push or args.list):
        ap.print_help(); return
    ssh, sftp = connect()
    try:
        if args.list: cmd_list(sftp)
        if args.pull: cmd_pull(sftp)
        if args.push: cmd_push(sftp, args.limit)
    finally:
        sftp.close(); ssh.close()

if __name__ == "__main__":
    main()
