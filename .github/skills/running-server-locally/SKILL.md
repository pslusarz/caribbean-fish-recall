---
name: running-server-locally
description: When user asks to run the server locally, or when they ask to verify a feature works. Local testing is an important last step in implementing a feature.
---

Since this is a webapp, the buck stops with the functionality being verified against a running server. Prefer curl for quick feedback; use the browser (or Playwright MCP, if connected) when the user wants to see it or when verification requires interacting with the page.

## Startup
To start the server with log rotation and port cleanup:
`./scripts/start_local.sh`
This script cleans up a previous running version, so you don't need to do anything extra there. It runs in hot-reload mode, so you should not have to restart it once it is running. It uses port 5002 (not 5001, which is barobeaver's port, in case both are running at once).

## Verification
Check the log prior to execution to establish a watermark, then execute and check the terminal output, and check for new log messages. Report findings back to the user, supported by evidence.

Confirm the server is running:
`curl -v http://localhost:5002/`

Start a lesson (POST, no body needed):
`curl -v -X POST http://localhost:5002/api/lesson/start`

Fetch the next item in a lesson:
`curl -v "http://localhost:5002/api/lesson/next_item?lesson_id=1"`

Submit an answer:
`curl -v -X POST http://localhost:5002/api/lesson/submit -H "Content-Type: application/json" -d '{"item_id": 1, "answer": "some-fish-id"}'`

Stats and browse:
`curl -v http://localhost:5002/api/stats`
`curl -v http://localhost:5002/api/browse`

A photo (served as a plain static file, not base64):
`curl -sI http://localhost:5002/photos/bar-jack_1.jpg`

## Debugging
Logs are available at:
`./data/app.log`
Tail that file between requests to see how they're affecting server behavior. The log can grow large — never read the whole file at once; increase the tail line count if you need more.
