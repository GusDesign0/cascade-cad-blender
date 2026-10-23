"""Blender-independent local mailbox for the warm Open CASCADE preview worker."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time


def replace_mailbox(source, destination):
    # Windows may briefly hold a read handle while the other process polls.
    for attempt in range(40):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if attempt == 39:
                raise
            time.sleep(0.001)


class PreviewEngine:
    def __init__(self, python, worker):
        self.folder = tempfile.TemporaryDirectory(prefix='cascade_preview_')
        self.path = Path(self.folder.name)
        self.sequence = 0
        self.seen = 0
        self.log = (self.path / 'engine.log').open('w', encoding='utf-8')
        self.process = subprocess.Popen([python, str(worker), '--live', str(self.path)],
            stdout=self.log, stderr=self.log,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

    def alive(self):
        return self.process.poll() is None

    def submit(self, request):
        if not self.alive():
            raise RuntimeError('O motor de previa encerrou. Reabra a ferramenta para reiniciar.')
        self.sequence += 1
        request = dict(request, id=self.sequence)
        temporary = self.path / 'request.tmp'
        temporary.write_text(json.dumps(request), encoding='utf-8')
        replace_mailbox(temporary, self.path / 'request.json')
        return self.sequence

    def poll(self):
        try:
            response = json.loads((self.path / 'response.json').read_text(encoding='utf-8'))
        except (FileNotFoundError, PermissionError, json.JSONDecodeError):
            return None
        if response['id'] <= self.seen:
            return None
        self.seen = response['id']
        return response

    def close(self):
        if self.alive():
            self.process.kill()
        self.process.wait()
        self.log.close()
        self.folder.cleanup()

