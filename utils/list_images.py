"""Print the image build matrix as JSON, read from the backend registry.

    uv run python utils/list_images.py              # every backend (PR builds)
    uv run python utils/list_images.py --published  # only released images

Each entry is ``{"backend": "github", "image": "fake-github"}``. CI feeds the
output to ``strategy.matrix`` with ``fromJSON``.
"""

import argparse
import json

from fake_backends.server.registry import BACKENDS, image_name, published_backends

__all__ = ["main", "matrix"]


def matrix(*, published_only: bool) -> list[dict[str, str]]:
    names = published_backends() if published_only else sorted(BACKENDS)
    return [{"backend": name, "image": image_name(name)} for name in names]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--published", action="store_true", help="only images a release publishes"
    )
    args = parser.parse_args()
    print(json.dumps(matrix(published_only=args.published)))


if __name__ == "__main__":
    main()
