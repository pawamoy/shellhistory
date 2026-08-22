# Changelog
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](http://keepachangelog.com/en/1.0.0/)
and this project adheres to [Semantic Versioning](http://semver.org/spec/v2.0.0.html).

<!-- insertion marker -->
## Unreleased

### Added

- Add local, batched Gitleaks scanning and safe command redaction through `shellhistory secrets`.

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
