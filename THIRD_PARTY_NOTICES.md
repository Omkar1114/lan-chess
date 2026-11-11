# Third-party notices

The source code written for LAN Chess is released under the [MIT License](LICENSE).
It builds on the third-party work listed below. This file explains what is
bundled in the repository, what is only downloaded at build time, and under
which terms.

## 1. Assets bundled in this repository

### Chess piece sets (`web/pieces/`)

The SVG files were taken, unmodified, from the
[lichess-org/lila](https://github.com/lichess-org/lila/tree/master/public/piece)
repository, whose `COPYING.md` documents the licences below.

| Folder     | Author                                                    | Licence |
| ---------- | --------------------------------------------------------- | ------- |
| `cburnett` | Colin M.L. Burnett                                        | Multi-licensed by the author: BSD 3-Clause, GFDL 1.2+ or GPL 2.0+ ([source](https://commons.wikimedia.org/wiki/Category:SVG_chess_pieces)). Used here under the **BSD 3-Clause** option. |
| `chessnut` | Alexis Luengas                                            | [Apache License 2.0](https://github.com/LexLuengas/chessnut-pieces/blob/master/LICENSE.txt) |
| `celtic`   | Maurizio Monge                                            | [MIT](https://github.com/maurimo/chess-art/blob/main/LICENSE) |
| `fantasy`  | Maurizio Monge                                            | [MIT](https://github.com/maurimo/chess-art/blob/main/LICENSE) |
| `rhosgfx`  | RhosGFX                                                   | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) (public domain) |

`web/favicon.svg` is the white knight of the `cburnett` set.

All of these licences allow redistribution inside an MIT-licensed project as
long as the attribution above is kept.

### Sounds

None are bundled. Move sounds are synthesised in the browser with the Web Audio
API (`web/js/sound.js`), so there are no audio files and no audio licences.

### Fonts

None are bundled. The interface uses the system font stack of the visiting
device (`system-ui`, `ui-monospace`, ...), which also keeps the app fully
offline.

## 2. Software installed at build time (not stored in this repository)

### Stockfish - GPL-3.0-or-later

<https://stockfishchess.org> · <https://github.com/official-stockfish/Stockfish>

* **Not in this repository.** No Stockfish source or binary is committed here.
* **How it gets into the Docker image:** the `Dockerfile` installs Debian's
  unmodified `stockfish` package with `apt-get`. Without Docker, you install
  Stockfish yourself with your package manager.
* **How it is used:** as a separate program. LAN Chess starts the `stockfish`
  executable as a child process and exchanges plain-text
  [UCI](https://www.chessprogramming.org/UCI) commands with it over
  stdin/stdout. Nothing is linked, imported or copied from Stockfish.
* **What that means:** Stockfish stays under the GPLv3 and LAN Chess's own code
  stays under MIT; shipping both in one image is "mere aggregation" in the
  GPL's terms. If you *distribute* an image that contains Stockfish (for
  example by pushing it to a public registry) you must comply with the GPLv3
  for Stockfish itself: keep its licence text (Debian ships it in
  `/usr/share/doc/stockfish/copyright`) and make the corresponding source
  available - Debian's source package (`apt-get source stockfish`) or the
  upstream repository at the matching version.

### python-chess - GPL-3.0-or-later

<https://github.com/niklasf/python-chess> (PyPI package `chess`)

Used for move generation and validation, PGN export and the UCI client. It is
installed from PyPI (`requirements.txt`) and is not vendored in this repository.

Unlike Stockfish, python-chess is *imported* by the server code. The MIT
licence is GPL-compatible, so this combination is permitted, with one
consequence worth knowing: the source in this repository remains MIT, but a
**combined distribution** that contains python-chess - such as a published
Docker image - must as a whole be offered under the terms of the GPLv3 (which
simply means: provide the source, which is this repository plus python-chess,
and keep the licence notices). Running the server on your own LAN is not
distribution and triggers no obligations.

### Other runtime dependencies

| Package | Licence |
| ------- | ------- |
| [FastAPI](https://github.com/fastapi/fastapi) | MIT |
| [Starlette](https://github.com/encode/starlette) | BSD 3-Clause |
| [Pydantic](https://github.com/pydantic/pydantic) | MIT |
| [Uvicorn](https://github.com/encode/uvicorn) | BSD 3-Clause |
| [websockets](https://github.com/python-websockets/websockets) | BSD 3-Clause |
| [uvloop](https://github.com/MagicStack/uvloop), [httptools](https://github.com/MagicStack/httptools) | MIT / Apache-2.0, MIT |
| [Python](https://www.python.org) and the Debian base image | PSF licence / various free licences |

Development-only tools (pytest, httpx, Ruff) are not part of the image.

## 3. Frontend

The browser code in `web/` is written for this project (MIT) and has no
third-party JavaScript or CSS dependencies.
