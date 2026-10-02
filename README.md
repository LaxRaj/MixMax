# producer

A CLI-only pipeline that analyzes, mixes, masters and QA-gates a raw vocal.

## Install

```bash
uv venv --python 3.11
uv pip install -e ".[dev]"
```

## Usage

```bash
producer master --vocal VOCAL.wav --reference REFERENCE.wav --out OUT.wav
```
