"""Offline regression for the same opt-in public-video acceptance runner."""
import json
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'validate_live_capture.py'


def test_acceptance_runner_works_without_network_or_downloads(tmp_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), '--synthetic', '--output-root', str(tmp_path), '--deadline', '30'],
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    reports = list(tmp_path.glob('*/result.json'))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text(encoding='utf-8'))
    assert report['passed'] is True and report['mode'] == 'synthetic'
    assert report['hardware_usb_verified'] is False
    assert len(report['cases']) == 6
    assert all(case['passed'] for case in report['cases'].values())
    playback = report['cases']['continuous_playback']
    assert playback['final_status']['frame_count'] == 30
    assert playback['final_status']['reason'] == 'end_of_file'
    assert playback['max_queue_depth'] <= 2
    assert playback['final_status']['dropped_frames'] > 0
    assert playback['end_to_end_p95_ms'] is None
    assert playback['frontend_disconnect_capture_continued']
    assert report['cases']['repeated_stop']['cycles'] == 3
    assert report['cases']['reader_failure']['actual_error']['code'] == 'capture_process_exited'
    assert not any(report['final_resources'].values())


@pytest.mark.parametrize('arguments', [[], ['--synthetic', '--fps', 'nan'], ['--synthetic', '--deadline', '0']])
def test_acceptance_runner_validates_cli_without_opening_devices(arguments):
    result = subprocess.run([sys.executable, str(SCRIPT), *arguments], capture_output=True, timeout=10)
    assert result.returncode == 2
    assert b'error:' in result.stderr
