# Completion checklist

When a task is complete, verify the relevant focused test(s) first, then the broader suite if the change is larger. For this repo, the standard end-to-end verification is `python -m pytest tests/`; contract changes should also include the targeted contract test file(s), and release-ready changes should build the Docker image with `docker build -t maker8:latest .`.