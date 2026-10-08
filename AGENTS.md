# Repository Guidelines

## Project Structure & Module Organization

This repository contains two independent applications:

- `backend/` is a Python 3.12 FastAPI package managed with `uv`. Runtime modules live in `backend/src/backend/`, Alembic revisions in `backend/alembic/`, and pytest suites in `backend/tests/`.
- `mobile/` is a Flutter application. Dart source belongs in `mobile/lib/`, widget tests in `mobile/test/`, and platform-specific wrappers in `android/`, `ios/`, `web/`, `macos/`, `linux/`, and `windows/`.

Run language-specific commands from the relevant subproject directory; use the root Compose file to launch both applications together.

## Build, Test, and Development Commands

From the repository root, `docker compose up --build` builds and starts both services. Flutter Web is exposed on port 8080 and the backend on port 8000.

From `backend/`:

- `uv sync` installs the Python 3.12 environment from `pyproject.toml` and `uv.lock`.
- `uv run backend` runs the registered console entry point.
- `uv run alembic upgrade head` applies PostgreSQL migrations.
- `uv run pytest` runs backend unit and API integration tests.
- `uv run ruff check src tests alembic` checks Python formatting and lint rules.
- `uv build` creates Python distribution artifacts.

From `mobile/`:

- `flutter pub get` resolves packages from `pubspec.yaml`.
- `flutter run` launches the app on a selected device or emulator.
- `flutter analyze` applies the configured Dart analyzer and lint rules.
- `flutter test` runs all Flutter tests; `flutter test test/auth_test.dart` runs the authentication tests directly.
- `flutter build apk` produces an Android release build.

## Coding Style & Naming Conventions

Use four-space indentation and `snake_case` for Python modules and functions. Keep backend modules under the `backend` package, type public functions, and run Ruff. For Dart, use `dart format .`; follow `flutter_lints`, two-space indentation, `lower_snake_case.dart` filenames, and `UpperCamelCase` widget classes.

## Testing Guidelines

Backend tests use pytest/pytest-asyncio; test files are named `test_*.py`. Mobile tests use `flutter_test` and `*_test.dart`. Authentication changes must cover OTP eligibility/expiry, refresh rotation and revocation, role injection denial, and the four-role permission matrix.

## Commit & Pull Request Guidelines

The `main` branch has no commits yet, so no established message convention exists. Start with concise, imperative, scoped subjects such as `mobile: add login screen` or `backend: add health endpoint`. Pull requests should identify the affected subproject, summarize behavior changes, list the commands run, and include screenshots for visible Flutter changes. No pull request template is currently configured.
