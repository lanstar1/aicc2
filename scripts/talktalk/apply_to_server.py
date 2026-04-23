"""Download affected product JSONs, replace talktalk_qna, re-upload."""
import json, paramiko, os, time, collections, io

with open('/tmp/tt_work/consolidated_by_rep.json','r',encoding='utf-8') as f:
    payload = json.load(f)
BY_REP = payload['reps']

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('${GODOMALL_SFTP_HOST}', port=17662, username='${GODOMALL_SFTP_USER}', password='${GODOMALL_SFTP_PASSWORD}')
sftp = ssh.open_sftp()

# backup tarball via SFTP: not practical. Just back up individual files on-demand.
ok = 0
skip_missing = 0
err = 0
empty_before = 0
entries_written = 0
examples = []

for rep, entries in BY_REP.items():
    remote = f'aicc2/products/{rep}.json'
    try:
        with sftp.open(remote, 'r') as f:
            data = f.read()
        if isinstance(data, bytes): data = data.decode('utf-8')
        d = json.loads(data)
    except FileNotFoundError:
        skip_missing += 1
        continue
    except Exception as e:
        err += 1
        print(f"[READ ERR] {rep}: {e}")
        continue

    if not d.get('talktalk_qna'):
        empty_before += 1

    # Build new talktalk_qna payload: keep transcript compact (max 30 entries)
    new_items = []
    cat_counter = collections.Counter()
    for c in entries:
        cat = c.get('category') or ''
        cat_counter[cat] += 1
        new_items.append({
            "source_model": c.get('source_model','') or rep,
            "chatUrl": c.get('chatUrl',''),
            "dateIso": c.get('dateIso',''),
            "name": c.get('name',''),
            "isTechnical": c.get('isTechnical'),
            "category": cat,
            "modelConfidence": c.get('modelConfidence',''),
            "question": c.get('question',''),
            "answer": c.get('answer',''),
            "turn_count": c.get('turn_count', 0),
            "transcript": c.get('transcript', [])[:16],  # cap transcript
        })

    d['talktalk_qna'] = new_items
    d['_talktalk_meta'] = {
        'qna_pair_count': len(new_items),
        'category_breakdown': dict(cat_counter),
        'consolidation': 'per_thread_v1',
        'consolidated_at': '2026-04-22T00:00:00Z',
    }

    try:
        new_bytes = json.dumps(d, ensure_ascii=False, indent=2).encode('utf-8')
        with sftp.open(remote, 'wb') as f:
            f.write(new_bytes)
        ok += 1
        entries_written += len(new_items)
        if len(examples) < 3:
            examples.append((rep, len(new_items)))
    except Exception as e:
        err += 1
        print(f"[WRITE ERR] {rep}: {e}")

print(f"\nUpdated: {ok}")
print(f"Missing on server: {skip_missing}")
print(f"Errors: {err}")
print(f"Entries written total: {entries_written}")
print(f"Examples: {examples}")

sftp.close(); ssh.close()
