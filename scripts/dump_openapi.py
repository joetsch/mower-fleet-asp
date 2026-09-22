"""Write the FastAPI OpenAPI schema to a file, for `openapi-typescript` to turn into
`frontend/src/api/schema.ts` (known-hazards "Refactor before Iteration 5" item 5).

    uv run python scripts/dump_openapi.py frontend/openapi.json

The frontend's `npm run types:generate` calls this and then runs `openapi-typescript`
over the output; `npm run types:check` (part of the frontend gate) does the same and
fails on any diff. So a response-shape change in `api/schemas.py` or `model.py` that
isn't reflected in the committed `openapi.json` / `schema.ts` trips the gate instead of
silently diverging from the hand-written mirror this replaced.

`sort_keys=True` keeps the output stable regardless of declaration order, so a pure
reordering in the pydantic models produces no diff.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from fleetplanning.api.app import app


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("frontend/openapi.json")
    out.write_text(json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
