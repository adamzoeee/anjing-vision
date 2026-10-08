"""Curated, content-verified scan46 delivery; never runs reconstruction.

Run from backend: python scripts/scan46_delivery.py --help
Only the explicitly listed files are restored, not historical failed candidates.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import shutil
import sqlite3
import zipfile
import zlib

MANIFEST = 'scan46_delivery_manifest.json'
POST = 'data/work/46/postprocess/'
RUNTIME = [
    POST + name for name in (
        'window_wall_joined_20261008.ply', 'window_wall_joined_20261008.visual.json',
        'preview_selection.json', 'alignment.json', 'layout.json',
        'structure.json', 'structure_calibrated.json', 'structure_plan.png',
        'measurements.json', 'passage_analysis.json', 'passage_analysis.png',
        'spatial_metrics.json', 'spatial_foundation.json', 'risk_assessment.json',
        'local_gaps_20260912.ply', 'local_gaps_20260912.visual.json',
    )
] + ['data/work/46/images/formal_risks.png', 'data/work/46/report/report.pdf',
     'data/media/46/1000044487.mp4']


def safe_name(name):
    value = name.replace('\\', '/')
    path = PurePosixPath(value)
    if (not value.startswith('data/') or path.is_absolute() or
            '..' in path.parts or ':' in value or '\x00' in value):
        raise ValueError(f'unsafe path: {name}')
    return path.as_posix()


def digest(stream):
    sha, crc, size = hashlib.sha256(), 0, 0
    while block := stream.read(8 * 1024 * 1024):
        sha.update(block)
        crc = zlib.crc32(block, crc)
        size += len(block)
    return size, crc & 0xffffffff, sha.hexdigest()


def snapshot(database):
    with sqlite3.connect(f'{database.resolve().as_uri()}?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        scan = dict(db.execute('SELECT * FROM scans WHERE id=46').fetchone())
        project = dict(db.execute('SELECT * FROM projects WHERE id=?',
                                  (scan['project_id'],)).fetchone())
        org = dict(db.execute('SELECT * FROM organizations WHERE id=?',
                              (project['org_id'],)).fetchone())
        report = dict(db.execute('SELECT * FROM reports WHERE scan_id=46').fetchone())
        report['images'] = json.dumps([Path(p.replace('\\', '/')).name
                                      for p in json.loads(report['images'] or '[]')])
        user = {'id': 3, 'org_id': org['id'], 'name': '交接演示',
                'email': 'demo@demo.com', 'password_hash': 'disabled-' + secrets.token_hex(24),
                'role': 'admin', 'created_at': org['created_at']}
        names = ('organizations', 'users', 'projects', 'scans', 'reports')
        schema = [db.execute('SELECT sql FROM sqlite_master WHERE type=? AND name=?',
                             ('table', name)).fetchone()[0] for name in names]
        return {'schema': schema, 'tables': dict(zip(names, [[org], [user], [project], [scan], [report]]))}


def archive_index(handles):
    index = {}
    for handle in handles:
        for info in handle.infolist():
            if not info.is_dir() and info.filename.replace('\\', '/').startswith('data/'):
                index[safe_name(info.filename)] = (handle, info)
    return index


def build(backend, archives, output, readme):
    handles = [zipfile.ZipFile(p) for p in archives]
    try:
        index = archive_index(handles)
        names = list(RUNTIME)
        # Ten original supplement clips and normal window2 inputs already supplied.
        for name in index:
            if (name.startswith('data/training_sources/scan46_buchong2/') and name.endswith('.mp4') or
                '/new2/window2/' in name and
                (name.endswith(('.npz', '.jpg', '.png')) or name.endswith('/manifest.json'))):
                names.append(name)
        # Keep a small provenance record; do not include failed candidate meshes.
        root = backend / 'data/work/46/local_gaps_20260912/window_wall_joined_20261008'
        for name in ('local_wall_review.json', 'wall_upright_visual.json',
                     'local_wall_candidate.json', 'occlusion_cleanup.json', 'local_wall_capture_manifest.json'):
            path = root / name
            if path.is_file():
                names.append(path.relative_to(backend).as_posix())
        records, additions = [], []
        for name in dict.fromkeys(names):
            source = backend / name
            previous = index.get(name)
            if source.is_file():
                with source.open('rb') as stream:
                    size, crc, sha = digest(stream)
                same = previous and (previous[1].file_size, previous[1].CRC) == (size, crc)
                origin = Path(previous[0].filename).name if same else output.name
                mtime = source.stat().st_mtime_ns
                if not same:
                    additions.append((source, name))
            elif previous:
                with previous[0].open(previous[1]) as stream:
                    size, crc, sha = digest(stream)
                origin, mtime = Path(previous[0].filename).name, None
            else:
                raise FileNotFoundError(f'Required delivery data missing: {name}')
            records.append({'path': name, 'size': size, 'crc32': crc, 'sha256': sha,
                            'source_archive': origin, 'mtime_ns': mtime})
        manifest = {'format': 1, 'scan_id': 46,
                    'accepted_file': 'window_wall_joined_20261008.ply',
                    'files': records, 'snapshot': snapshot(backend / 'anjing-local.db'),
                    'scope': 'Current scan46 preview and business report, not account history or retraining.',
                    'archive_order': [Path(p).name for p in archives] + [output.name]}
        output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=1) as z:
            for source, name in additions:
                z.write(source, name, compress_type=zipfile.ZIP_STORED if source.suffix == '.mp4' else None)
            z.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2))
            z.writestr('交接说明.md', readme.read_text(encoding='utf-8'))
        if output.stat().st_size >= 1_000_000_000:
            raise ValueError('Bundle exceeds WeChat 1 GB; split before delivery')
        print(json.dumps({'zip': str(output), 'bytes': output.stat().st_size,
                          'new_files': len(additions), 'reused_files': len(records)-len(additions)}, ensure_ascii=False))
    finally:
        for handle in handles:
            handle.close()


def restore(bundle, previous, backend):
    database = backend / 'scan46-handoff.db'
    if database.exists():
        raise FileExistsError('Use a clean backend directory; existing handoff database is preserved')
    handles = [zipfile.ZipFile(p) for p in [*previous, bundle]]
    try:
        manifest = json.loads(handles[-1].read(MANIFEST))
        index = archive_index(handles)
        files = []
        # Validate all paths and contents before writing anything.
        for record in manifest['files']:
            name = safe_name(record['path'])
            if name not in index:
                raise FileNotFoundError(f'Missing {name}; provide all three previous scan46 archives')
            handle, info = index[name]
            with handle.open(info) as stream:
                size, _, sha = digest(stream)
            if size != record['size'] or sha != record['sha256']:
                raise ValueError(f'checksum mismatch: {name}')
            target = (backend / name).resolve()
            if not target.is_relative_to(backend.resolve()):
                raise ValueError(f'unsafe path: {name}')
            if target.exists():
                with target.open('rb') as stream:
                    _, _, existing_sha = digest(stream)
                if existing_sha != sha:
                    raise FileExistsError(f'Existing different data preserved: {target}')
            files.append((handle, info, target, record))
        for handle, info, target, record in files:
            target.parent.mkdir(parents=True, exist_ok=True)
            with handle.open(info) as stream, target.open('wb') as destination:
                shutil.copyfileobj(stream, destination, 8 * 1024 * 1024)
            if record.get('mtime_ns') is not None:
                os.utime(target, ns=(record['mtime_ns'], record['mtime_ns']))
        with sqlite3.connect(database) as db:
            db.execute('PRAGMA foreign_keys=ON')
            for ddl in manifest['snapshot']['schema']:
                db.execute(ddl)
            for table, rows in manifest['snapshot']['tables'].items():
                if table not in ('organizations', 'users', 'projects', 'scans', 'reports'):
                    raise ValueError('Invalid snapshot table')
                for original in rows:
                    row = dict(original)
                    if table == 'reports':
                        row['images'] = json.dumps([str((backend / 'data/work/46/images' / Path(p).name).resolve())
                                                    for p in json.loads(row['images'])])
                    columns = ','.join('"' + column + '"' for column in row)
                    db.execute(f'INSERT INTO "{table}" ({columns}) VALUES ({",".join("?" for _ in row)})',
                               list(row.values()))
        print(f'Restored {len(files)} verified files. Database: {database}')
    finally:
        for handle in handles:
            handle.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    b = sub.add_parser('build')
    b.add_argument('--backend', type=Path, default=Path.cwd())
    b.add_argument('--previous', type=Path, nargs='+', required=True)
    b.add_argument('--output', type=Path, required=True)
    b.add_argument('--readme', type=Path, required=True)
    r = sub.add_parser('restore')
    r.add_argument('--bundle', type=Path, required=True)
    r.add_argument('--previous', type=Path, nargs='*', default=[])
    r.add_argument('--backend', type=Path, default=Path.cwd())
    args = parser.parse_args()
    if args.command == 'build':
        build(args.backend.resolve(), args.previous, args.output, args.readme)
    else:
        restore(args.bundle, args.previous, args.backend.resolve())


if __name__ == '__main__':
    main()
