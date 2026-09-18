import asyncio

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from gridwise.interpreter import ModelError, configuration, interpret
from gridwise.models import Plan, Scenario
from gridwise.optimizer import InfeasibleError, optimize
from gridwise.replay import verify

load_dotenv()
app = FastAPI(title="GridWise", version="1.0.0",
              description="LLM-assisted, verified 24-hour campus energy optimization.")


@app.exception_handler(RequestValidationError)
async def invalid_request(request, exc):
 
    issues = [{"field": ".".join(str(part) for part in e["loc"]),
               "message": e["msg"], "type": e["type"]} for e in exc.errors()]
    return JSONResponse(status_code=400, content={"error": "invalid_request",
                        "message": "Fix the listed fields and send the scenario again",
                        "details": issues})


@app.get("/", include_in_schema=False)
async def index():
    return RedirectResponse("/docs")


@app.get("/health")
async def health():
    try:
        if not configuration()[1]:
            raise ModelError("Not configured")
    except ModelError:
        return JSONResponse(status_code=503, content={"status": "not_ready"})
    return {"status": "ok"}


def solve_and_verify(scenario, directives):
    plan = optimize(scenario, directives)
    verify(scenario, plan)
    return plan


@app.post("/optimize-energy", response_model=Plan)
async def optimize_energy(scenario: Scenario):
    try:
        directives = await asyncio.wait_for(interpret(scenario), timeout=20)
        return await run_in_threadpool(solve_and_verify, scenario, directives)
    except InfeasibleError:
        return JSONResponse(status_code=422, content={"error": "infeasible_scenario",
                            "message": "No feasible schedule satisfies the interpreted constraints"})
    except ModelError as exc:
        return JSONResponse(status_code=500, content={"error": "interpretation_unavailable",
                            "message": str(exc)})
    except TimeoutError:
        return JSONResponse(status_code=500, content={"error": "interpretation_unavailable",
                            "message": "The AI provider exceeded the 20-second interpretation deadline. Try again later."})
    except Exception:
        return JSONResponse(status_code=500, content={"error": "optimization_failed",
                            "message": "A verified schedule could not be produced"})
