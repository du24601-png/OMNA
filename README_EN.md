<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="brand/OMNA_Horizontal_White.svg">
    <img src="brand/OMNA_Horizontal_Black.svg" alt="OMNA" width="220">
  </picture>
</p>

<p align="center"><b>Switch AI tools without introducing yourself all over again.</b></p>

<p align="center">Windows desktop app · 1.0.0 · Your data stays on your device</p>

<p align="center"><a href="README.md"><img alt="简体中文" src="https://img.shields.io/badge/%E7%AE%80%E4%BD%93%E4%B8%AD%E6%96%87-Read-687386?style=for-the-badge"></a> <a href="README_EN.md"><img alt="English" src="https://img.shields.io/badge/English-Current-252a32?style=for-the-badge"></a></p>

OMNA (知我) is a local personal memory tool that keeps your background, preferences, and project context in one place for use across AI tools. AI helps organize your memories; you decide what to keep and share, and can review or correct every item.

See the [US go-to-market strategy deck (PPTX, 25 slides, Chinese)](docs/gtm/OMNA_US_GTM_Strategy_v1.pptx) for the proposed marketing approach and execution plan.

## Video demo

> The demo video is in progress. The complete workflow will be shown here when it is ready.

## Screenshots

<sub>Screenshots use synthetic demo data.</sub>

**About me** · Review confirmed memories by topic, generate an AI summary on demand, and see how often each Agent accesses your memories.

![About me: confirmed memories, AI summary, and Agent access activity](docs/images/about-me.png)

| Review · Approve AI-suggested memories | My Agents · Manage connections and access |
| --- | --- |
| ![Review: new and updated memories suggested by an Agent](docs/images/review.png) | ![My Agents: connection status and permissions](docs/images/agents.png) |

**Memories** · Find memories by topic, source, or status; inspect version history, edit an item, or stop sharing it.

![Memory list: topic, source, and date](docs/images/memories.png)

## How it works

![OMNA architecture: you manage local memories and AI tools access them through MCP permissions](docs/images/architecture-en.svg)

## Four tools for Agents

OMNA exposes only these four MCP tools to Agents. There is no direct interface for changing or deleting memories, or reading imported source text.

| Tool | What it does |
| --- | --- |
| `get_context` | Retrieve relevant memories allowed for the current task |
| `search_memory` | Search current memories within permitted categories |
| `propose_memory` | Suggest a new or updated memory for your review and approval |
| `explain_memory` | View the reviewed evidence excerpt for an accessible memory |

Every call is filtered by connection, tool, category, sharing status, and expiry. Unshared, private, expired, unapproved, and superseded memories are not returned.

Supported clients include WorkBuddy, ZCode, OpenCode, ChatGPT (Codex), Claude Desktop, and Claude Code. Other stdio MCP clients can use a generated configuration pasted into the client manually.

## Local storage and privacy

- Memories, sources, pending proposals, permissions, and access records are stored on your device, by default in `%APPDATA%\OMNA\data`.
- The Chinese embedding model (`bge-small-zh-v1.5`) ships with the installer and runs locally.
- The local service listens only on `127.0.0.1:8765`. The desktop app generates an access credential at first launch; you do not have to enter one yourself.
- No telemetry, cloud sync, or background collection.

There are two cases where data leaves your device:

1. When extracting suggestions from an import or generating an AI summary, OMNA sends that request's text to the model service you configured. Manual entry, viewing, and search work without a configured model.
2. If an Agent you authorize uses a cloud-hosted model, memories returned to it travel with that Agent's request. Revoking access prevents future reads but cannot recall content already sent.

## Installation

**Requirements:** Windows 10 / 11 x64 and about 700 MB of disk space. Python, Node.js, and other runtimes do not need to be installed separately.

1. Download `OMNA-Setup-1.0.0.exe` (about 210 MB) from [Releases](../../releases).
2. The installer is currently unsigned, so Windows SmartScreen may warn that it is an unrecognized app. If you trust the source, select **More info** → **Run anyway**.
3. Install for the current user without administrator access. You can choose the install folder; shortcuts are added to the desktop and Start menu.
4. The local model may take 20–30 seconds to load the first time. Later launches usually take a few seconds.

**While running:** Closing the window minimizes OMNA to the system tray, leaving the Agent service available. Use the tray menu to reopen the window, open the log folder, restart the service, or quit completely. The service log is at `%APPDATA%\OMNA\logs\service.log`.

**Uninstalling:** Uninstall from Windows Settings or the Start menu. Your memories in `%APPDATA%\OMNA` are kept. To remove them, first use **Settings → Clear data**, or delete that folder manually after uninstalling.

## Connect an Agent

1. Open **My Agents**. OMNA lists the clients it detects on this device.
2. Select a client, choose **Connect**, then choose **Read only** or **Can propose changes**. OMNA adds its MCP configuration to that client's config file and preserves existing entries.
3. New connections can read **Preferences** and **Goals** by default. Grant other categories in the Agent's **Permissions**.
4. Restart the client and send it the example prompt shown in OMNA (for example, “Check my preferences”). The connection is marked **Verified** after the first successful call.

In **Access history**, you can see which tool each Agent called and which memories were returned.

## Current limitations

- Windows x64 only; the installer is unsigned.
- The local service uses port 8765. If another program occupies it, OMNA reports the problem and stops instead of connecting to that program.
- End-to-end calls with a real client have been verified only with OpenCode. Configuration writing was checked for the other supported clients.
- The installer has been tested on the development machine for installation, startup, saving, search, MCP calls, restart, and uninstall. A clean machine without development tools and a fully offline run after installation have not been tested separately.
- Chinese retrieval achieved Hit@5 of 19/20 on 20 fixed synthetic queries: 10/10 direct queries and 9/10 rewritten queries. This small sample is not a measure of real-world user outcomes, and retrieval may include memories that are not closely related to a query. See the [query set](experiments/kernel_spike/fixtures/p0_3_queries.json) and [evaluation results](experiments/kernel_spike/results/p0_3_windows.json).

## Build from source

### Requirements

- Windows 10 / 11 x64
- [uv](https://docs.astral.sh/uv/) 0.11 or later (it installs the locked CPython 3.12.13 automatically)
- Node.js 24 and pnpm 11.21 (`corepack enable`)
- To build the installer, x64 Visual C++ Redistributable 14.40 or later on the build machine

### Install dependencies and the model

```powershell
pnpm install
uv sync --directory server
```

The Chinese embedding model is not included in the repository. Download it once with an internet connection (about 90 MB):

```powershell
uv run --directory server python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-zh-v1.5', cache_dir=r'D:\omna-model')"
```

After download, the model is at `D:\omna-model\models--Qdrant--bge-small-zh-v1.5`.

### Run in development

Run the local service and frontend separately. Use a new, isolated data directory; do not point development at a memory library you already use.

```powershell
$env:ZHIWO_DATA_DIR = "D:\omna-dev-data"
$env:ZHIWO_OWNER_CREDENTIAL = "dev-only-credential"
$env:ZHIWO_FASTEMBED_CACHE_DIR = "D:\omna-model"
uv run --directory server uvicorn zhiwo.api.app:app --host 127.0.0.1 --port 8765
```

In another terminal:

```powershell
pnpm --filter @zhiwo/web dev
```

Open `http://127.0.0.1:5173` in your browser and enter the credential above. Configure an extraction model in Settings, or set `ZHIWO_EXTRACTOR_BASE_URL`, `ZHIWO_EXTRACTOR_MODEL`, and `ZHIWO_EXTRACTOR_API_KEY` to use any OpenAI-compatible API.

### Build the installer

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File apps\desktop\scripts\prepare-runtime.ps1 -ModelSource D:\omna-model\models--Qdrant--bge-small-zh-v1.5
pnpm --filter @zhiwo/desktop dist
```

`prepare-runtime` creates the bundled Python runtime, locked dependencies, frontend build, and model under `apps/desktop/runtime/`. `dist` outputs `apps/desktop/dist/OMNA-Setup-1.0.0.exe`. If GitHub downloads are slow, set `ELECTRON_MIRROR` and `ELECTRON_BUILDER_BINARIES_MIRROR` to your preferred mirrors.

### Tests

Tests use synthetic data in temporary directories. For example:

```powershell
server\.venv\Scripts\python.exe tests\test_client_connect.py
server\.venv\Scripts\python.exe tests\test_embedding_probe.py
```

Tests that use a real client require OpenCode to be installed; set `OPENCODE_BIN` to its executable if needed. `test_p2_4_client.py` fails when OpenCode is unavailable. `test_p3_2_desktop.py` requires a built installer and records the OpenCode check as `NOT_RUN` if it cannot find the client.

## Tech stack

- Desktop: Electron 44 for the window, local service, system tray, and single-instance behavior; Node integration is disabled in the renderer
- UI: React 19, Vite, and Tailwind CSS
- Local service: Python 3.12, FastAPI, uvicorn, and the MCP Python SDK
- Memory kernel: [Mnemosyne](https://pypi.org/project/mnemosyne-memory/) 3.15.1 with local fastembed vectors
- Storage: two SQLite databases. Mnemosyne is the sole source of truth for confirmed memories; `zhiwo.db` stores sources, pending proposals, permissions, and access records

```text
apps/web        UI
apps/desktop    Electron main process, packaging scripts, and installer config
server/zhiwo    Local service, MCP stdio bridge, and Mnemosyne adapter
tests           Service and acceptance tests; evidence is under results/
experiments     Early technical validation scripts and results
brand           Logos and app icons
```

## Project documents

| Document | Contents |
| --- | --- |
| [PRODUCT.md](PRODUCT.md) | Product scope, pages, and interaction rules (Chinese) |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Module boundaries, data ownership, and API contracts (Chinese) |
| [AGENTS.md](AGENTS.md) | AI development guidelines (Chinese) |

## License

Original code in this project is released under the [MIT License](LICENSE), copyright OMNA. Third-party dependencies, the bundled model, and third-party brand assets remain subject to their respective licenses or terms of use.
