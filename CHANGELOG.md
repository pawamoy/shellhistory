# Changelog
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](http://keepachangelog.com/en/1.0.0/)
and this project adheres to [Semantic Versioning](http://semver.org/spec/v2.0.0.html).

<!-- insertion marker -->
## [0.3.0](https://github.com/pawamoy/shell-history/releases/tag/0.3.0) - 2026-10-06

<small>[Compare with 0.2.4](https://github.com/pawamoy/shell-history/compare/0.2.4...0.3.0)</small>

### Build

- Drop support for Python 3.10 ([07b3714](https://github.com/pawamoy/shell-history/commit/07b3714014ae8da6b523606884d83d0e5906e2e6) by Timothée Mazzucotelli).

### Features

- Add secret detection and redaction ([913b972](https://github.com/pawamoy/shell-history/commit/913b9722642320f6ce59098d7ed787251277dc1a) by Timothée Mazzucotelli).
- Add more charts ([0ab6c98](https://github.com/pawamoy/shell-history/commit/0ab6c98adb2096295c962a8a5433fcb14bf4f3c8) by Timothée Mazzucotelli).
- Add optional type normalization to the type chart ([d9c46dd](https://github.com/pawamoy/shell-history/commit/d9c46dd70354dc17a6f2191b089a0283d167de57) by Timothée Mazzucotelli).

### Bug Fixes

- Fix various chart issues ([41ef818](https://github.com/pawamoy/shell-history/commit/41ef818828fcf5f7d742ab827e0987f51af6eb47) by Timothée Mazzucotelli).
- Fix word cloud in home page ([6174574](https://github.com/pawamoy/shell-history/commit/61745743e4b3c7eed87849209b6a61fe32cfbad8) by Timothée Mazzucotelli).
- Serve every chart when the database is empty ([b5089b6](https://github.com/pawamoy/shell-history/commit/b5089b6e5a3202962e168da058613a5e770b910b) by Timothée Mazzucotelli).
- Restore what the template upgrade dropped ([ce852e5](https://github.com/pawamoy/shell-history/commit/ce852e59534407e987f0f78559cf529a2ba6c07b) by Timothée Mazzucotelli).
- Return the real exit code from the early-return path ([6747846](https://github.com/pawamoy/shell-history/commit/6747846a1570f28fb89cc8a7c836ef9e45ba908f) by Timothée Mazzucotelli).
- Make shellhistory enable and disable idempotent ([a8285a5](https://github.com/pawamoy/shell-history/commit/a8285a54e95f34b9291b7435f1ce89cc463fe668) by Timothée Mazzucotelli).
- Support BSD and macOS date and base64 ([2b52408](https://github.com/pawamoy/shell-history/commit/2b52408e277dfb45e09aa972ceb720fce8bdae07) by Timothée Mazzucotelli).
- Record the running shell instead of the login shell ([f4d59c3](https://github.com/pawamoy/shell-history/commit/f4d59c3b303d3be165bf758ef215f2ae0d808066) by Timothée Mazzucotelli).
- Do not record commands the user asked to keep private ([411acc2](https://github.com/pawamoy/shell-history/commit/411acc23389b52e277bb8d242bb15efa479fae17) by Timothée Mazzucotelli).
- Read the zsh command from preexec instead of history[HISTCMD] ([eb1370b](https://github.com/pawamoy/shell-history/commit/eb1370b5b5c5a6f68bfeb63264903bfdad5e65d2) by Timothée Mazzucotelli).
- Use extend_existing instead of the removed useexisting ([d7ccef9](https://github.com/pawamoy/shell-history/commit/d7ccef93a984375fa6f6a77d17c329b3f89ccc40) by Timothée Mazzucotelli).
- Create database tables after the model is declared ([b27f237](https://github.com/pawamoy/shell-history/commit/b27f2372c7c6136db56856d750e426e9720e5e0c) by Timothée Mazzucotelli).
- Possibly fix for sqlite thread issue ([03d4f3e](https://github.com/pawamoy/shell-history/commit/03d4f3e1e64c609e2802e7421872fb57350bd419) by Timothée Mazzucotelli). [Issue-22](https://github.com/pawamoy/shell-history/issues/22)
- Fix listing processes with unusual characters ([283c5db](https://github.com/pawamoy/shell-history/commit/283c5db8d354e9ec4edfd0a94bc222ae09f9c6a8) by Timothée Mazzucotelli).

### Performance Improvements

- Avoid forking on every prompt and at shell startup ([460fc18](https://github.com/pawamoy/shell-history/commit/460fc189648055244fcb560f36e7a3b7b19ad4e7) by Timothée Mazzucotelli).

### Code Refactoring

- Upgrade Flask ([b2f2868](https://github.com/pawamoy/shell-history/commit/b2f286852dd601465cb5ac9a810d2e9272c122b3) by Timothée Mazzucotelli).
- Rename the sync button to say what it now does ([2a70d36](https://github.com/pawamoy/shell-history/commit/2a70d36f0b4e58ac4a23574e45721483b2dabb7a) by Timothée Mazzucotelli).
- Have the shell primitives assign instead of echoing ([bebc9be](https://github.com/pawamoy/shell-history/commit/bebc9becd348833cfd23e5b9f17157cffc8ed64a) by Timothée Mazzucotelli).

## [0.2.4](https://github.com/pawamoy/shellhistory/releases/tag/0.2.4) - 2019-05-17

<small>[Compare with 0.2.3](https://github.com/pawamoy/shellhistory/compare/0.2.3...0.2.4)</small>

### Fixed
- Fix stupid mistake (not importing codecs module).
- Fix trap on DEBUG not being removed when calling `shellhistory disable` ([a4f9d2e](https://github.com/pawamoy/shellhistory/commit/a4f9d2ed6094a54ba856a6693e53d93dea09f39f)).
- Ignore exceptions when reading history file (log on stderr) ([45e3d16](https://github.com/pawamoy/shellhistory/commit/45e3d16695a8c941360604c8ae5d7d9d7fe3ba7e)).


## [0.2.3](https://github.com/pawamoy/shellhistory/releases/tag/0.2.3) - 2019-05-04

<small>[Compare with 0.2.2](https://github.com/pawamoy/shellhistory/compare/0.2.2...0.2.3)</small>

### Fixed
- Fix python version specifier in pyproject ([07f305d](https://github.com/pawamoy/shellhistory/commit/07f305d5b4ad4adfed154febd0cbfa557bc8fca4)).


## [0.2.2](https://github.com/pawamoy/shellhistory/releases/tag/0.2.2) - 2019-04-30

<small>[Compare with 0.2.1](https://github.com/pawamoy/shellhistory/compare/0.2.1...0.2.2)</small>

### Docs
- Add note about performance for shell startup ([42ed881](https://github.com/pawamoy/shellhistory/commit/42ed88184b03fe977e596b3f86075e7c428703c8)).
- Update pipx installation instructions ([44b5c15](https://github.com/pawamoy/shellhistory/commit/44b5c152c03b35acd1fbd3f4e9db70630facbf89)).

### Fixed
- Fix admin internal error (sqlite threads) ([a699eac](https://github.com/pawamoy/shellhistory/commit/a699eac45d5b38b275b65721f20b254433e10499)).
- Fix shell code for ZSH ([05795cf](https://github.com/pawamoy/shellhistory/commit/05795cf5030257444a8e9b9f199f8c0ef060e238)).
- Correctly set and restore precmd, preexec, PROMPT_COMMAND and debug trap ([7336a8c](https://github.com/pawamoy/shellhistory/commit/7336a8c347fb0d593b74e3b501f51cf484eb4afd)).
- Ignore invalid characters in history file ([e8229cd](https://github.com/pawamoy/shellhistory/commit/e8229cd0dd34fe8858344d502c967a9b76f8deb1)).


## [0.2.1](https://github.com/pawamoy/shellhistory/tags/0.2.1) - 2018-12-26

<small>[Compare with 0.2.0](https://github.com/pawamoy/shellhistory/compare/0.2.0...0.2.1)</small>

- Implement new charts ([4434afd](https://github.com/pawamoy/shellhistory/commit/4434afdce557f861f0b6b32b5ecd8c0474b59029)).
- Fix shellhistory command (return, don't exit) ([a27fb53](https://github.com/pawamoy/shellhistory/commit/a27fb53e097f22acc7cf789fb69f244208115c3f)).

## [0.2.0](https://github.com/pawamoy/shellhistory/tags/0.2.0) - 2018-12-23

<small>[Compare with 0.1.0](https://github.com/pawamoy/shellhistory/compare/0.1.0...0.2.0)</small>

- Package the application ([edaf15b](https://github.com/pawamoy/shellhistory/commit/edaf15b7424d40ef13442be03ae04828eb80571d)).

  The application is now packaged as a Python package. It is easier to install and setup.
  - To install it: `pip install shellhistory`.
  - To enable it: `. $(shellhistory-location); shellhistory enable` at shell startup.

  **BREAKING CHANGES:**
  - The default directory in which `shellhistory` reads the history file and write the SQlite3 database file
  is now `~/.shellhistory` instead of `~/.shell_history`.
  - The SQlite3 database file is now named `db.sqlite3` instead of `db`.

    The migration is very easy:
    - rename the directory: `mv ~/.shell_history ~/.shellhistory`,
    - and the database file: `mv ~/.shellhistory/db ~/.shellhistory/db.sqlite3`.

    You can even skip renaming the database file:
    delete it and reimport your data with `shellhistory-cli --import`

  Please remember this application is alpha software, and is subject to change without guarantee of backward compatibility.

## [0.1.0](https://github.com/pawamoy/shellhistory/tags/0.1.0) - 2018-04-23

<small>[Compare with first commit](https://github.com/pawamoy/shellhistory/compare/4a9781fb20047c4c5f9d7bd04f60db5e35295070...0.1.0)</small>

- Initial version without packaging.
