# AGENTS.md

## Cursor Cloud specific instructions

This is a Chinese-language **AI 菜谱内容生成批处理工具**（"一页厨/一页厨师图片生成器"）. It is a Python CLI/batch pipeline (no web server, no database, no long-running daemon). It reads a dish idea, generates structured recipe copy + text-to-image prompts, renders images, optionally scores/selects them, and optionally publishes to Douyin via Playwright.

### Runtime
- Python 3.12 is available as `python3` (there is no `python` alias). Always invoke scripts with `python3`.
- Dependencies (`openai`, `Pillow`, `playwright`) are installed via the startup update script (`pip install -r requirements.txt`) into the user site-packages. No virtualenv is used.
- `playwright install chromium` is NOT run by default (browsers are only needed for the optional Douyin publishing step, which also requires a logged-in Douyin session). Run it manually if you specifically need to test `tools/douyin_publish.py`.

### Config precedence (important)
Config is loaded as: process env vars > `.env` > `config.env` (see `ensure_runtime_config_loaded` in `image_generator.py`).
- Public tunables live in `config.env` (checked in): models, image sizes/quality/counts, timeouts, Photoshop paths, publish topics.
- Secrets go in a git-ignored `.env` (see `.env.example`).

### Required secrets to run the AI pipeline (not present in this environment)
The full pipeline requires paid API keys and cannot run end-to-end without them:
- `DOUBAO_API_KEY` — default text-generation provider (`TEXT_API_PROVIDER=doubao`) and the multimodal image-review model. Endpoint: Volcengine Ark (`https://ark.cn-beijing.volces.com/api/v3`).
- `OPENAI_API_KEY` — image generation (`gpt-image-2`), and text if `TEXT_API_PROVIDER=openai`.

Without keys, `main.py` correctly runs through config load + idea file read and then fails with a clear `未找到 DOUBAO_API_KEY` message — this confirms the environment is set up; only the paid credentials are missing.

### Running on Linux (this environment)
`config.env` defaults assume a Windows workstation with Photoshop. When running here, override the Windows-only / optional steps via env vars:
- `PHOTOSHOP_AUTO_COMPOSITE=2` — disable local Photoshop compositing (Photoshop is Windows-only and not installed).
- `PUBLISH_AUTO_SELECT=2` — optional; skips the multimodal image-review/selection step.
- The Douyin publish tail step only triggers if a `publish/` folder exists in the output dir, so it is naturally skipped otherwise.

### Entry points / commands
- Full pipeline: `python3 main.py` (text → images → select → Photoshop → Douyin tail).
- Text + prompts only (no image model): `python3 tools/generate_text_prompts_from_dish_name.py`.
- See `README.md` for the complete list of `tools/*.py` entry points and Windows helper scripts.

### Tests & lint
- There is **no automated test suite and no configured linter** (no pytest/ruff/flake8 config).
- The de-facto regression test is `python3 tools/check_prompt_pollution.py` — it runs with NO API keys: it builds a local recipe bundle, renders local page text for `guide_pages/page02..06`, checks for pollution keywords, and runs `py_compile` on the core modules. Exit code 0 = pass.
- Quick compile check: `python3 -m py_compile main.py image_generator.py guide_generator.py linshicankao.py script_logging.py tools/*.py guide_pages/*.py`.

### Notes
- All scripts tee stdout/stderr to timestamped files under `logs/` (git-ignored) via `script_logging.py`.
- The local content engine (`build_local_recipe_bundle` + `guide_pages.*.build_local_page_text`) generates full recipe guide copy without any API call — useful for offline verification of core content logic.
