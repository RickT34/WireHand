"""Generate SDK reference HTML directly from public Python signatures/docstrings."""
import argparse
from pathlib import Path

MODULES = ('wirehand.factory', 'wirehand.automation.computer', 'wirehand.automation.recording.model',
           'wirehand.automation.recording.recorder', 'wirehand.vision.matching', 'wirehand.automation.inspection')


def build(output):
    import pdoc
    pdoc.render.configure(docformat='google', show_source=False, search=True)
    pdoc.pdoc(*MODULES, output_directory=Path(output))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    build(parser.parse_args().output)


if __name__ == '__main__':
    main()
