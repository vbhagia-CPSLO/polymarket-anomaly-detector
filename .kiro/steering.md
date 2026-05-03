# Steering

## Python Execution

Always activate the virtualenv before running Python:

```
source .venv/bin/activate && python ...
```

Do not use `.venv/bin/python` directly.

## Code Style

Write only the minimal amount of code needed to address the requirement. Avoid verbose implementations, unnecessary abstractions, and any code that doesn't directly contribute to the solution. No defensive boilerplate beyond what the task requires.

Match the existing project style: async httpx for HTTP, dataclasses for models, plain sqlite3 (no ORM), f-strings, single-file modules. Do not introduce new libraries or patterns unless explicitly asked.

## Testing

After any code change, run `python -m pytest tests/ -q` to verify nothing is broken before presenting the result. Fix failures before responding.

## Database

The SQLite DB at `./polymarket.db` is the source of truth for what the system has seen. When inspecting data or debugging, query it directly with `sqlite3 polymarket.db`. Do not assume DB state — check it.

## LLM / Ollama

The Ollama endpoint is `http://sunils-mac-studio:11434` and the model is `qwen2.5:14b`. Both are configured in `config.py` via environment variables. Do not hardcode these values in new code.

## Validation

The validation harness is `validate.py`. Run it with `python validate.py` for full results or `python validate.py --sweep` for the volatility baseline grid search. It fetches live data from Polymarket — expect it to take 60-120 seconds.

