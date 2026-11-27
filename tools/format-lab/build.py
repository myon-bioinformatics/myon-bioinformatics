"""Publish the standalone Format Lab and its offline ZIP into a fresh directory."""
import argparse
from pathlib import Path
import shutil
import zipfile


def publish(destination):
    source = Path(__file__).resolve().parent
    assets = ('index.html', 'app.js', 'formats.js', 'style.css', 'verify.py',
              'inspect_format.py', 'vendor/js-yaml/js-yaml.min.js', 'vendor/js-yaml/LICENSE.txt')
    destination.mkdir(parents=True)
    with zipfile.ZipFile(destination / 'format-lab.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in assets:
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, target)
            archive.write(target, name)
    index = destination / 'index.html'
    index.write_text(index.read_text(encoding='utf-8').replace('<!-- FORMAT_LAB_DOWNLOAD -->',
                     '<a class="source" download href="format-lab.zip">↓　最小構成を保存</a>'), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    publish(parser.parse_args().destination)
