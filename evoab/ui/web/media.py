"""Mirror published observations without asking the environment to capture."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from evoab.services.environment.client import EnvironmentClient


class SnapshotReader:
    def __init__(self, url, root, *, on_preview=None):
        self.client = EnvironmentClient(url, timeout_s=2)
        self.root = Path(root)
        self.seen = set()
        self.info = None
        self.on_preview = on_preview

    def poll(self, stopped=None):
        if self.info is None:
            self.info = self.client.connect()
        snapshot = self.client.latest_snapshot()
        if snapshot.get('episode_id') != self.info.episode_id:
            raise ValueError('Environment episode changed; refusing unrelated preview')
        sid = snapshot['snapshot_id']
        if sid in self.seen:
            return None
        cameras, images = {}, {}
        temporary_preview = self.info.environment.metadata.get('image_recording_stride') is not None
        directory = self.root / 'monitor_media'
        if not temporary_preview:
            directory.mkdir(exist_ok=True)
        for name, frame in snapshot.get('cameras', {}).items():
            if stopped is not None and stopped.is_set():
                return None
            ref = frame['rgb']
            if ref.get('media_type') not in {'image/png', 'image/jpeg', 'image/webp'}:
                continue
            data = self.client.artifact_bytes(ref['artifact_id'])
            suffix = {'image/png': '.png', 'image/jpeg': '.jpg', 'image/webp': '.webp'}[ref['media_type']]
            filename = hashlib.sha256(data).hexdigest() + suffix
            if temporary_preview:
                key = 'preview/' + filename
                images[key] = (ref['media_type'], data)
            else:
                path = directory / filename
                if not path.exists():
                    temporary = path.with_suffix('.tmp')
                    temporary.write_bytes(data)
                    temporary.replace(path)
                key = 'monitor_media/' + filename
            cameras[name] = {'path': key}
        if stopped is not None and stopped.is_set():
            return None
        record = {key: snapshot.get(key) for key in ('snapshot_id', 'captured_at', 'state_version', 'state')}
        record['cameras'] = cameras
        if temporary_preview:
            if self.on_preview is not None:
                self.on_preview(record, images)
        else:
            with (self.root / 'monitor_frames.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + '\n')
        self.seen.add(sid)
        return self.info

    def close(self):
        self.client.close()
