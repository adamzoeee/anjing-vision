import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import zipfile

import pytest

SCRIPT = Path(__file__).parents[1] / 'scripts' / 'scan46_delivery.py'


def module():
    spec = importlib.util.spec_from_file_location('delivery', SCRIPT)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def fixture_zip(path, content=b'new geometry', entry='data/work/46/postprocess/result.ply'):
    snapshot = {'schema': ['CREATE TABLE reports (id INTEGER, images TEXT)'],
                'tables': {'reports': [{'id': 1, 'images': '["formal_risks.png"]'}]}}
    manifest = {'files': [{'path': entry, 'size': len(content),
                         'sha256': hashlib.sha256(content).hexdigest(), 'mtime_ns': 1000000000}],
                'snapshot': snapshot}
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr(entry, content)
        z.writestr('scan46_delivery_manifest.json', json.dumps(manifest))


def test_restore_preserves_geometry_and_relocates_image_paths(tmp_path):
    archive = tmp_path / 'bundle.zip'
    fixture_zip(archive)
    destination = tmp_path / 'different-machine'
    module().restore(archive, [], destination)
    assert (destination / 'data/work/46/postprocess/result.ply').read_bytes() == b'new geometry'
    with sqlite3.connect(destination / 'scan46-handoff.db') as db:
        images = json.loads(db.execute('SELECT images FROM reports').fetchone()[0])
    assert images == [str((destination / 'data/work/46/images/formal_risks.png').resolve())]


def test_bad_checksum_writes_no_data_or_database(tmp_path):
    archive = tmp_path / 'bad.zip'
    fixture_zip(archive)
    with zipfile.ZipFile(archive, 'a') as z:
        z.writestr('data/work/46/postprocess/result.ply', b'corrupt')
    with pytest.raises(ValueError, match='checksum'):
        module().restore(archive, [], tmp_path / 'target')
    assert not (tmp_path / 'target/data').exists()


def test_path_escape_is_rejected(tmp_path):
    archive = tmp_path / 'escape.zip'
    fixture_zip(archive, entry='data/../../escape.ply')
    with pytest.raises(ValueError, match='path'):
        module().restore(archive, [], tmp_path / 'target')
    assert not (tmp_path / 'escape.ply').exists()


def test_existing_database_is_not_overwritten(tmp_path):
    archive = tmp_path / 'bundle.zip'
    fixture_zip(archive)
    target = tmp_path / 'target'
    target.mkdir()
    (target / 'scan46-handoff.db').write_bytes(b'preserve')
    with pytest.raises(FileExistsError):
        module().restore(archive, [], target)
    assert (target / 'scan46-handoff.db').read_bytes() == b'preserve'


def test_new_bundle_wins_over_old_selection(tmp_path):
    entry = 'data/work/46/postprocess/preview_selection.json'
    old, new = tmp_path / 'old.zip', tmp_path / 'new.zip'
    with zipfile.ZipFile(old, 'w') as z:
        z.writestr(entry, b'wrong old selection')
    fixture_zip(new, content=b'accepted current selection', entry=entry)
    target = tmp_path / 'target'
    module().restore(new, [old], target)
    assert (target / entry).read_bytes() == b'accepted current selection'
