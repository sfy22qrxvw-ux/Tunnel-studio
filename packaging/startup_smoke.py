# Copyright (c) 2026 Damian Gulczynski.
"""Isolated startup regression check run before activating a Windows release.

The caller creates a new temporary TUNNEL_STUDIO_DATA directory before importing
the application. No real survey workspace is opened by this check.
"""
from pathlib import Path
import hashlib


PROBE_BYTES = 21_000_000
CHUNK_BYTES = 1_000_000


def _file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(CHUNK_BYTES), b''):
            digest.update(block)
    return digest.hexdigest()


def _text_hash(value):
    digest = hashlib.sha256()
    for start in range(0, len(value), CHUNK_BYTES):
        digest.update(value[start:start + CHUNK_BYTES].encode('utf-8'))
    return digest.hexdigest()


def verify_startup(server, projects, temporary_root):
    root = Path(temporary_root).resolve(strict=True)
    if not root.name.startswith('TunnelStudio_setup_check_') or Path(server.WORK).resolve() != root:
        raise RuntimeError('Startup self-check requires its isolated temporary workspace.')
    source = root / 'project.json'
    named_folder = root / 'projects'
    if source.exists() or (named_folder.exists() and any(named_folder.iterdir())):
        raise RuntimeError('Startup self-check refuses to replace existing project data.')

    # Write the oversized fixture in 1 MB pieces. Do not create additional
    # whole-file strings merely to generate or hash the check data.
    content_hash = hashlib.sha256()
    metadata_hash = hashlib.sha256()
    block = b'x' * CHUNK_BYTES
    with source.open('xb') as stream:
        def write(value):
            stream.write(value)
            metadata_hash.update(value)
        write(b'{"project_title":"Installer startup self-check","axis":null,"cloud":null,"project_id":null,"report_settings":{"installer_probe":"')
        remaining = PROBE_BYTES
        while remaining:
            part = block[:min(CHUNK_BYTES, remaining)]
            write(part)
            content_hash.update(part)
            remaining -= len(part)
        write(b'"}}')
    original_bytes = source.stat().st_size
    if original_bytes <= 20_000_000:
        raise RuntimeError('Startup self-check fixture does not exceed the former limit.')

    server.start_empty()
    if server.STATE.get('cloud') is not None or server.STATE.get('axis') is not None or server.STATE.get('job_items'):
        raise RuntimeError('Startup self-check did not open an empty workspace.')
    if server.JOB.get('error'):
        raise RuntimeError('Startup metadata self-check failed: ' + str(server.JOB['error']))
    current = projects.read_json(source)
    if current.get('project_title') != 'Untitled project' or current.get('report_settings'):
        raise RuntimeError('Startup self-check did not persist the empty workspace.')

    recoveries = list((root / 'startup_recovery').glob('*.json'))
    if len(recoveries) != 1 or recoveries[0].stat().st_size != original_bytes:
        raise RuntimeError('Startup self-check did not preserve the entire prior autosave.')
    recovery_hash = _file_hash(recoveries[0])
    if recovery_hash != metadata_hash.hexdigest():
        raise RuntimeError('Startup self-check changed the prior autosave bytes.')
    # Startup recovery is intentionally hidden. Creating a second named project
    # here would duplicate the previous session in Open Project and keep stale
    # caches owned indefinitely. Exercise the actual recovery metadata reader,
    # while requiring startup to leave the normal project listing empty.
    if (named_folder.exists() and any(named_folder.iterdir())) or projects.listing(root):
        raise RuntimeError('Startup self-check created an unwanted named previous session.')
    restored = projects.read_json(recoveries[0])
    value = restored.get('report_settings', {}).get('installer_probe')
    if not isinstance(value, str) or len(value) != PROBE_BYTES or _text_hash(value) != content_hash.hexdigest():
        raise RuntimeError('Startup self-check could not read the complete hidden recovery metadata.')
    return {
        'passed': True,
        'fixture_metadata_bytes': original_bytes,
        'fixture_data': 'Synthetic metadata only; no scan points or user project paths.',
        'fixture_sha256': metadata_hash.hexdigest(),
        'recovery_sha256': recovery_hash,
        'recovery_metadata_content_sha256': content_hash.hexdigest(),
        'startup_opened_blank': True,
        'empty_workspace_persisted': True,
        'previous_autosave_bytes_preserved': True,
        'hidden_recovery_metadata_reopened': True,
        'named_previous_sessions_created': 0,
        'isolated_workspace_verified': True,
    }
