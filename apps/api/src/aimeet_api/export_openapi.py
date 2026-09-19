"""Export the OpenAPI contract without connecting to the database."""

import argparse
import json
from pathlib import Path

from aimeet_api.main import app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
