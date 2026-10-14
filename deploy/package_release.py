"""Identity and Git boundary for an offline, immutable deployment package."""
import json
from pathlib import Path
import re
import subprocess

PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def is_fixed_package():
    # Presence is enough to block updates, including a damaged manifest.
    return ((PACKAGE_ROOT / 'RELEASE_VERSION.json').exists()
            or (PACKAGE_ROOT / 'PACKAGE_PATCHES.json').exists())


def read_package_release():
    data = json.loads((PACKAGE_ROOT / 'RELEASE_VERSION.json').read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError('Package release must be an object')
    for key in ('repository', 'branch', 'commit', 'release', 'built_at'):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ValueError('Invalid package field: ' + key)
    if not re.fullmatch(r'[0-9a-f]{40}', data['commit']):
        raise ValueError('Invalid package commit')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', data['repository']):
        raise ValueError('Invalid package repository')
    if data.get('auto_update') is not False:
        raise ValueError('A fixed package must disable automatic updates')
    return data


def apply_fixed_package_config(config):
    if is_fixed_package():
        try:
            release = read_package_release()
        except (OSError, ValueError):
            # Stay closed even if metadata is damaged. The updater reports
            # this error, and the launcher rejects the package checksum.
            release = {'repository': 'tomoe1-1/OAS-tomoe', 'branch': 'self'}
        config.update(
            Repository='https://github.com/' + release['repository'] + '.git',
            Branch=release['branch'], AutoUpdate=False,
            InstallDependencies=False, EnableReload=False,
        )


def has_own_git_repository(git_executable):
    # Never let Git discover the repository of a parent directory.
    git_entry = PACKAGE_ROOT / '.git'
    if not git_entry.exists() or git_entry.is_symlink():
        return False
    try:
        result = subprocess.run(
            [git_executable, 'rev-parse', '--show-toplevel', '--absolute-git-dir'], cwd=PACKAGE_ROOT,
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=5, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        lines = result.stdout.strip().splitlines()
        if result.returncode or len(lines) != 2 or Path(lines[0]).resolve() != PACKAGE_ROOT:
            return False
        git_dir = Path(lines[1]).resolve()
        if git_entry.is_dir():
            return git_dir == git_entry
        # A genuine linked worktree has a back-reference to this .git file.
        backlink = git_dir / 'gitdir'
        return backlink.is_file() and Path(backlink.read_text(encoding='utf-8').strip()).resolve() == git_entry
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return False
