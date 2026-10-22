"""Bundle a validated recording, script and referenced images for download."""
import io
from pathlib import Path
import zipfile

from .model import Recording


def export_archive(directory):
    directory = Path(directory)
    flow = Recording.load(directory, allow_incomplete=True)
    if flow.data['status'] == 'recording':
        raise ValueError('Stop recording before downloading')
    output = io.BytesIO()
    assets = {target['image'] for target in flow.data['targets'].values()}
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
        for name in ['recording.json', 'replay.py', *sorted(assets)]:
            bundle.write(directory / name, f'{directory.name}/{name}')
    return output.getvalue()
