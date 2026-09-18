# GridWise — BUP CSE Fest 2026

HTTP API for LLM-assisted, minimum-cost campus energy scheduling. This folder
contains the complete runtime source, dependency list, deployment configuration
and submission documentation. No files from the parent folder are needed for deployment.

## Run locally

Requires Python 3.12. In this folder, on Windows:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
# If .env is missing: Copy-Item .env.example .env
# Set OPENROUTER_API_KEY in .env.
.\.venv\Scripts\python.exe -m uvicorn app:app --host 0.0.0.0 --port 8000
```

On Linux/macOS: create the environment with `python3.12 -m venv .venv`, then use
`.venv/bin/python` in place of `.\.venv\Scripts\python.exe` in the commands above.
Keep the server terminal open. Restart after editing `.env`.

## Deploy to Vercel

1. Put **the contents of this folder** at the root of your GitHub repository.
2. In Vercel, select **Add New → Project** and import that repository.
3. Select the **FastAPI** framework preset if not detected automatically.
   Leave build/output overrides at their defaults. If you imported the entire
   parent project instead, set **Root Directory** to `submission`.
4. Add these **Production environment variables**:

   | Name | Value |
   |---|---|
   | `LLM_PROVIDER` | `openrouter` |
   | `OPENROUTER_API_KEY` | Your OpenRouter key |
   | `LLM_MODEL` | `nex-agi/nex-n2.5-mini:free` |

5. Click **Deploy**. Vercel loads `app.py` and installs `requirements.txt`.
6. Use the production URL in Postman:

   ```text
   GET  https://YOUR-PROJECT.vercel.app/health
   POST https://YOUR-PROJECT.vercel.app/optimize-energy
   ```

7. Ensure the production URL has no Vercel Authentication requirement so the judge
   can call it. After changing environment variables, redeploy.

The local `.env` is already copied into this folder for local use. On Vercel,
enter those values in the project environment settings; `.env` is excluded from
Git, deployment uploads and Docker builds. No Vercel deployment has been performed yet.

Official references: [FastAPI on Vercel](https://vercel.com/docs/frameworks/backend/fastapi)
and [environment variables](https://vercel.com/docs/environment-variables).

## API and Postman

Send **one scenario object** to `POST /optimize-energy`, using **Body → raw → JSON**
and `Content-Type: application/json`. Postman needs no Authorization header.
Interactive API documentation is available at `/docs`; the schema is at `/openapi.json`.

Required request fields:

- `scenario_id`: nonempty string.
- `operator_notes`: 1–3 nonempty natural-language strings.
- `hours`: exactly 24 entries, each with unique `hour` 0–23, `demand_kwh`,
  `solar_kwh` and `tariff_bdt_per_kwh`.
- `battery`: `capacity_kwh`, `initial_energy_kwh`, `minimum_energy_kwh`,
  `max_charge_kwh_per_hour`, `max_discharge_kwh_per_hour`.

Energy/tariff values must be finite nonnegative numbers, not quoted strings.
Battery minimum <= initial energy <= capacity. Windows include the start hour
and exclude the end hour.

Successful output includes `scenario_id`, one `directive_interpretation` per note,
24 `hourly_plan` entries, `total_grid_kwh`, `total_cost_bdt`, `peak_grid_kwh`
and `plan_summary`. Each plan hour contains grid/solar usage, battery action,
battery action magnitude and ending battery energy.

```powershell
curl.exe http://127.0.0.1:8000/health
curl.exe -X POST http://127.0.0.1:8000/optimize-energy -H "Content-Type: application/json" --data-binary "@YOUR-REQUEST.json"
```

Health returns `{"status":"ok"}` with HTTP 200 when the app is loaded and the
provider key is configured. It does not make a model call or check remaining quota.
Errors: 400 with field details for invalid input; 422 for infeasible interpreted
constraints; controlled 500 for provider/solver failure; 503 health if unconfigured.

## Public-sample test

Use the organizer's original `BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json`.
Start the API, then paste this into PowerShell in this folder, changing the pack path
and base URL if needed. It uses the installed runtime dependencies.

```powershell
@'
import json, time
from pathlib import Path
import httpx
from gridwise.models import Scenario, Plan, Interpretation
from gridwise.replay import verify

pack = Path(r'C:\path\to\BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json')
base_url = 'http://127.0.0.1:8000'
cases = json.loads(pack.read_text(encoding='utf-8'))['cases']
with httpx.Client(timeout=30) as client:
    for case in cases:
        r = client.post(base_url + '/optimize-energy', json=case['input'])
        assert r.status_code == 200, (case['id'], r.status_code)
        plan = Plan.model_validate(r.json())
        expected = case['expected_output']
        ds = Interpretation(directive_interpretation=expected['directive_interpretation']).directive_interpretation
        assert [d.model_dump(exclude={'explanation'}) for d in plan.directive_interpretation] == [d.model_dump(exclude={'explanation'}) for d in ds]
        verify(Scenario.model_validate(case['input']), plan.model_copy(update={'directive_interpretation': ds}), tolerance=0.01)
        assert abs(plan.total_cost_bdt - expected['total_cost_bdt']) <= 0.01
        print('PASS', case['id'], plan.total_cost_bdt)
        time.sleep(4)
'@ | .\.venv\Scripts\python.exe -
```

Expected result is ten PASS lines. Each request uses real AI inference and provider
quota. Equivalent optimal action sequences are accepted; the test compares semantic
directives, constraints and cost rather than exact hourly action sequences.

## Architecture and configuration

Flow: request validation → LLM interpretation → deterministic guardrails → HiGHS
optimization → independent hourly replay → JSON response.

The model interprets solar reductions, minimum reserves, no-charge/no-discharge
windows, grid caps and irrelevant notes. Guardrails check schemas, note ordering,
hours, numeric ranges, applies/no_op semantics and unambiguous explicit AM/PM
ranges. No phrase-matching interpreter or reference-answer lookup replaces the LLM.

SciPy's HiGHS mixed-integer solver minimizes grid energy times tariff. It enforces
energy balance, solar availability, battery bounds/rates, exclusive charge/discharge,
all interpreted directives and final battery energy equal to initial energy.
The final plan is independently replayed before returning it. The model has a
20-second interpretation deadline and at most one retry; the solver has a
three-second limit and must establish optimality.

Environment variables: `LLM_PROVIDER`, `OPENROUTER_API_KEY`, `LLM_MODEL`.
Optional Groq alternative: set `LLM_PROVIDER=groq`, `GROQ_API_KEY` and
`LLM_MODEL=openai/gpt-oss-120b`. OpenRouter is restricted to free model IDs;
there is no automatic paid fallback. `PORT` controls the Docker port, default 8000.

Dependencies and credits: FastAPI/Starlette, Pydantic, SciPy/NumPy/HiGHS, HTTPX,
Uvicorn and python-dotenv. OpenRouter/Groq supply model inference. OpenAI Codex
assisted implementation and verification. Challenge specifications and examples
come from the organizers.

Validation: 54 development tests passed. A real custom scenario passed semantic,
schedule and optimal-cost checks at 2220 BDT in approximately 3.2 seconds after
the AM/PM guardrail fix. The previous full live sample run was 9/10, with the failed
interpretation succeeding on retry; the full live suite has not been rerun since
that fix. Free-provider quotas, availability, latency and interpretation mistakes
remain limitations. Overlapping solar reductions are treated as caps relative to
the original forecast, using the lowest factor; the specification does not
explicitly define overlapping solar reductions.

## Docker fallback

With Docker installed, run from this folder:

```sh
docker build -t gridwise:1.0.0 .
docker run --rm --env-file .env -p 8000:8000 gridwise:1.0.0
```

Test `/health` and an optimization request. To publish the required fallback image:

```sh
docker tag gridwise:1.0.0 YOUR_DOCKERHUB_USER/gridwise:1.0.0
docker push YOUR_DOCKERHUB_USER/gridwise:1.0.0
docker pull YOUR_DOCKERHUB_USER/gridwise:1.0.0
docker run --rm --env-file .env -p 8000:8000 YOUR_DOCKERHUB_USER/gridwise:1.0.0
```

Replace the username and submit the actual tested tag/digest. The Dockerfile is
included; the image has not yet been built, verified or published. Vercel hosting
does not replace the required Docker fallback artifact.

## Submission

Submit the live API base URL, source repository, this README, tested pullable Docker
image reference, and a maximum three-minute solution video. The organizer requires
a repository created after reveal, private during the event and public after the
deadline. Model keys are configured through environment variables.

Video outline: 0:00–0:30 problem and inputs; 0:30–1:15 LLM extraction and guardrails;
1:15–2:00 optimizer and battery neutrality; 2:00–2:40 live request and test result;
2:40–3:00 deployment and local/Docker run steps. Record actual results. This outline
is documentation; the required video still needs to be recorded.
