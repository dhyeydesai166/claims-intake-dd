# Tool comparison

## Task

One paragraph in `README.md` that explains why the image is built with `--platform linux/amd64`, for a joiner already in the course container.

## How I used Cursor

I opened the ship worktree, pointed Cursor at the stub README and the Dockerfile, and asked it to write the platform note into README only. I committed in the terminal when the paragraph was right.

## What Cursor made easy

It could read the stub README (`app.main:app`, venv, pip) and the Dockerfile `CMD` in the same session. The paragraph could name the command this repo actually uses, and the why could sit next to `uv run uvicorn claims.api.routes:app` instead of a laptop install story.

## What Cursor made awkward

Asked for a paragraph, it rewrote the whole README and restated `docker buildx build --platform linux/amd64` as if restating were the explanation. I still had to insist the reason is this workspace often being ARM while GitHub `ubuntu-latest` and most servers are amd64.

## Preference

For **README and run docs that must match the checkout**, I would use **Cursor**. It can see the stub README and the Dockerfile. Writing that paragraph from memory is how you keep `app.main:app` and skip the ARM vs amd64 why.
