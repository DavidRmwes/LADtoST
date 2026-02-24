# LAD → ST Converter

Browser-based tool for converting Allen-Bradley L5X/L5K Ladder Logic (RLL) exports into IEC 61131-3 Structured Text.

Built with [Streamlit](https://streamlit.io) — no terminal access needed for end users.

## Project Structure

```
l5k-converter/
├── app.py               # Streamlit frontend
├── l5x_lad2st.py        # Conversion engine (also works standalone via CLI)
├── requirements.txt     # Python dependencies
├── .streamlit/
│   └── config.toml      # Streamlit theme & config
└── README.md
```

## Local Setup

```bash
# 1. Clone or copy the project
cd l5k-converter

# 2. Install dependencies (use a venv if preferred)
pip install -r requirements.txt

# 3. Run
streamlit run app.py
```

Opens in your browser at `http://localhost:8501`.

## Deploy to Streamlit Community Cloud (Free)

1. Push this folder to a GitHub repo (can be private)
2. Go to [share.streamlit.io](https://share.streamlit.io) and sign in with GitHub
3. Click **New app** → select your repo, branch, and `app.py`
4. Click **Deploy**

You'll get a public URL like `https://your-app.streamlit.app` — share that with users.

## Deploy via Docker

```bash
docker build -t lad2st .
docker run -p 8501:8501 lad2st
```

<details>
<summary>Dockerfile</summary>

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -r requirements.txt
EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
```
</details>

## Features

- **Upload** `.L5X` (XML) or `.L5K` (text) files
- **Select** specific routines or convert all
- **Combined** output (single file) or **Split** (one file per routine, downloaded as `.zip`)
- **Output as** `.st` or `.txt`
- **Strip NOPs** — omit NOP-only rungs
- **Simplify** — optimize always-true patterns like `EQU(X,X)`
- **Preview** output before downloading
- **Conversion report** with stats and review items

## CLI Usage (Advanced)

The converter engine still works standalone from the command line:

```bash
python l5x_lad2st.py input.L5X
python l5x_lad2st.py input.L5K -o output.st
python l5x_lad2st.py input.L5X --split --simplify --strip-nop
python l5x_lad2st.py input.L5X --list
```

Run `python l5x_lad2st.py --help` for all options.
