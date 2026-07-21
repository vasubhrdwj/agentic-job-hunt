# Demo workspace seed

With the API running on port 8000 and the frontend origin allowed at port 3000,
seed a fresh account from the repository root:

```bash
python3 scripts/seed_demo_workspace.py
```

The script uses only Python's standard library and the public HTTP API. It creates
a uniquely named local demo account, uploads `fixtures/sample_resume.txt` as the
base resume, adds a backend/platform career track, and adds a manual saved search
linked to that resume. It does not run a scan or require any provider/API key.

The JSON output includes the generated login credentials and created resource IDs.
Use `--base-url`, `--origin`, `--email`, `--password`, or `--resume` to override
the defaults; run `python3 scripts/seed_demo_workspace.py --help` for details.
