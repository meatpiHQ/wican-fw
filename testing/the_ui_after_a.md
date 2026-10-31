# The UI after a flash

Command: `ssh rpi001 'curl -s --compressed -D - -o /dev/null http://10.42.1.194/ui/setup.js'` then the same with `-H 'If-None-Match: <that ETag>'` and with an old tag

Needs: rpi001 + DUT

Moved from the `TESTING.md` test-kinds table on 2026-10-31 (the row's detail cell, whole, as paragraphs). The index row and the other stages: [../TESTING.md](../TESTING.md).

**The UI after a flash** (2026-10-08, Ali: "i just flashed a new device but the changes did not take effects"): the embedded assets carried `Cache-Control: public, max-age=3600` and an ETag of the file's size alone (`W/"97be-0"`), so a browser that had the page from another build at the same address (192.168.0.10) kept it for an hour without asking, and even asking got 304 when the sizes matched. Now `Cache-Control: no-cache` and `ETag: W/"<size>-<build ELF hash prefix>"` (`W/"97be-e6af21af"` on `9663ad1-dirty`): a matching tag answers `304 Not Modified`, the previous build's tag answers `200` with the new page, `/` the same (`W/"1b7ab-e6af21af"`); files fetched onto the filesystem keep the hour. A phone that still shows the old page after THIS build was flashed holds the old `max-age` from before: reload past the cache once
