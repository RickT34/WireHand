"""Named script bundles stored locally with validated, transactional ZIP imports."""
from contextlib import contextmanager
from datetime import datetime, timezone
import io
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import unicodedata
import uuid
import zipfile

from wirehand.core.locking import exclusive_lock
from wirehand.automation.recording import Recorder, Recording

MAX_ARCHIVE = 64 * 1024 * 1024
MAX_FILES = 1000


class NameConflict(ValueError):
    def __init__(self, existing):
        super().__init__('已有同名脚本，是否替换？')
        self.existing = existing


def validate_name(value):
    if not isinstance(value, str):
        raise ValueError('请输入脚本名称')
    value = unicodedata.normalize('NFC', value.strip())
    if (not 1 <= len(value) <= 80 or value in ('.', '..')
            or any(c in '/\\:*?"<>|' or unicodedata.category(c).startswith('C') for c in value)
            or value.endswith('.')):
        raise ValueError('名称须为 1–80 个字符，不能包含路径或特殊字符')
    return value


class ScriptLibrary:
    def __init__(self, root):
        self.root = Path(root).resolve()

    @contextmanager
    def locked(self):
        with exclusive_lock(self.root, 'library'):
            yield

    def path(self, identity):
        if not isinstance(identity, str) or not re.fullmatch('[a-f0-9]{32}', identity):
            raise FileNotFoundError('脚本不存在')
        path = self.root / identity
        if path.is_symlink() or not path.is_dir():
            raise FileNotFoundError('脚本不存在')
        return path

    def info(self, identity):
        directory = self.path(identity)
        meta = json.loads((directory / 'script.json').read_text()) if (directory / 'script.json').exists() else {
            'name': identity, 'revision': 'legacy', 'created_at': datetime.fromtimestamp(directory.stat().st_mtime, timezone.utc).isoformat()}
        manifest = directory / 'recording.json'
        data = json.loads(manifest.read_text()) if manifest.exists() else {'status': 'complete', 'steps': []}
        return {'id': identity, **meta, 'status': data['status'], 'steps': len(data['steps']),
                'runnable': data['status'] == 'complete' and (directory / 'replay.py').is_file()}

    def list(self):
        if not self.root.exists():
            return []
        items = []
        for directory in self.root.iterdir():
            if not re.fullmatch('[a-f0-9]{32}', directory.name):
                continue
            try:
                items.append(self.info(directory.name))
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return sorted(items, key=lambda item: item['created_at'], reverse=True)

    def named(self, name, excluding=None):
        return next((item for item in self.list() if item['name'].casefold() == name.casefold()
                     and item['id'] != excluding), None)

    def _metadata(self, directory, name, *, created_at=None):
        data = {'name': name, 'revision': uuid.uuid4().hex,
                'created_at': created_at or datetime.now(timezone.utc).isoformat()}
        temporary = directory / 'script.tmp'
        temporary.write_text(json.dumps(data, ensure_ascii=False))
        temporary.chmod(0o600)
        temporary.replace(directory / 'script.json')

    def record(self, backend, name=None):
        name = validate_name(name) if name else '录制-' + datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:4]
        with self.locked():
            existing = self.named(name)
            if existing:
                raise NameConflict(existing)
            recorder = Recorder(backend, self.root / uuid.uuid4().hex)
            self._metadata(recorder.directory, name)
            return recorder

    def rename(self, identity, name):
        name = validate_name(name)
        with self.locked():
            info = self.info(identity)
            existing = self.named(name, identity)
            if existing:
                raise NameConflict(existing)
            self._metadata(self.path(identity), name, created_at=info['created_at'])
            return self.info(identity)

    def delete(self, identity):
        with self.locked():
            shutil.rmtree(self.path(identity))

    def import_zip(self, raw, filename, *, replace_id=None, revision=None):
        if not isinstance(filename, str) or not filename.lower().endswith('.zip'):
            raise ValueError('请上传 ZIP 脚本压缩包')
        name = validate_name(filename[:-4])
        if len(raw) > MAX_ARCHIVE:
            raise ValueError('压缩包不能超过 64 MiB')
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.import-', dir=self.root) as temporary:
            staged = Path(temporary) / 'bundle'
            staged.mkdir(mode=0o700)
            try:
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                    members = archive.infolist()
                    if len(members) > MAX_FILES or sum(m.file_size for m in members) > MAX_ARCHIVE:
                        raise ValueError('解压后不能超过 64 MiB 或 1000 个文件')
                    files = {}
                    for member in members:
                        path = PurePosixPath(member.filename)
                        mode = member.external_attr >> 16
                        if (not path.parts or path.is_absolute() or '..' in path.parts
                                or '\\' in member.filename or ':' in member.filename
                                or stat.S_ISLNK(mode) or member.flag_bits & 1
                                or (stat.S_IFMT(mode) and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)))):
                            raise ValueError('压缩包包含不安全的路径或文件类型')
                        if member.is_dir():
                            continue
                        if str(path) in files:
                            raise ValueError('压缩包包含重复文件')
                        files[str(path)] = member
                    roots = [PurePosixPath(n).parent for n in files if PurePosixPath(n).name == 'replay.py']
                    if len(roots) != 1:
                        raise ValueError('压缩包必须包含唯一的 replay.py')
                    root = roots[0]
                    for name_in_zip, member in files.items():
                        path = PurePosixPath(name_in_zip)
                        if not path.is_relative_to(root):
                            raise ValueError('所有文件必须位于 replay.py 所在目录中')
                        relative = path.relative_to(root)
                        if any(part.startswith('.') or part == '__pycache__' for part in relative.parts):
                            raise ValueError('压缩包不能包含隐藏文件或缓存')
                        if str(relative) == 'script.json':
                            continue  # Server owns display names and replacement revisions.
                        target = staged.joinpath(*relative.parts)
                        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                        target.write_bytes(archive.read(member))
                        target.chmod(0o600)
                script = (staged / 'replay.py').read_bytes()
                if len(script) > 2 * 1024 * 1024:
                    raise ValueError('replay.py 不能超过 2 MiB')
                compile(script, 'replay.py', 'exec')
                if (staged / 'recording.json').exists():
                    Recording.load(staged, allow_incomplete=True)
            except (zipfile.BadZipFile, SyntaxError, UnicodeError, KeyError, TypeError, AttributeError, RuntimeError) as exc:
                raise ValueError(f'无效的脚本压缩包：{exc}') from exc
            with self.locked():
                existing = self.named(name)
                if existing and (replace_id != existing['id'] or revision != existing['revision']):
                    raise NameConflict(existing)
                if replace_id and not existing:
                    raise ValueError('原脚本已经改变，请重新上传')
                identity = existing['id'] if existing else uuid.uuid4().hex
                destination = self.root / identity
                self._metadata(staged, name)
                backup = Path(temporary) / 'previous'
                if existing:
                    destination.rename(backup)
                try:
                    staged.rename(destination)
                except BaseException:
                    if backup.exists():
                        backup.rename(destination)
                    raise
                return self.info(identity)

    def export(self, identity):
        with self.locked():
            directory = self.path(identity)
            info = self.info(identity)
            if info['status'] == 'recording':
                raise ValueError('请先停止录制')
            output = io.BytesIO()
            total = 0
            with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(directory.rglob('*')):
                    relative = path.relative_to(directory)
                    if path.is_symlink():
                        raise ValueError('脚本目录不能包含符号链接')
                    if not path.is_file() or '__pycache__' in relative.parts or path.name == 'script.json':
                        continue
                    total += path.stat().st_size
                    if total > MAX_ARCHIVE or len(archive.filelist) >= MAX_FILES:
                        raise ValueError('脚本文件超出压缩包大小限制')
                    archive.write(path, f'{identity}/{relative.as_posix()}')
            return info['name'], output.getvalue()
