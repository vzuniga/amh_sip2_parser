# AMH SIP2 Flask Prototype

This is the first Flask wrapper around the standalone AMH SIP2 parser.

## What it does

- Upload a `.txt` or `.log` SIP2 log file
- Parse and pair `09` check-in requests with `10` check-in responses
- Extract key SIP2 fields such as `AB`, `AQ`, `AJ`, `AF`, `CR`, `CT`, and `CL`
- Classify routing outcomes
- Display a summary and a preview table in the browser
- Export parsed rows to CSV for later sorting-matrix matching

## File structure

- `app.py` - Flask app entry point
- `parser.py` - standalone SIP2 parser module
- `templates/index.html` - upload form and results page
- `requirements.txt` - Python dependencies

## Run locally

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Then open:

```text
http://127.0.0.1:5000/
```

