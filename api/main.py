"""Aplicación FastAPI de Atlas — tablero de camas (capa de presentación).

La API es una capa FINA: traduce HTTP ↔ servicios de dominio. Sin lógica de negocio propia.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.routers import camas, internaciones
from domain.discharge_checklist_service import AltaConPasosPendientes
from domain.reservation_service import ReservaTipoInvalido
from domain.state_machine import TransicionInvalida
from domain.transition_service import ReversionSinInternacion, RolNoAutorizado

app = FastAPI(
    title="Atlas — Gestión de Camas",
    description="API REST del tablero de camas. Capa 1a.",
    version="0.1.0",
)

# CORS: permite el front local durante desarrollo.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------------------------------------------------ #
# Mapeo de excepciones de dominio → HTTP
# ------------------------------------------------------------------ #

@app.exception_handler(TransicionInvalida)
async def handle_transicion_invalida(request: Request, exc: TransicionInvalida):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(RolNoAutorizado)
async def handle_rol_no_autorizado(request: Request, exc: RolNoAutorizado):
    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.exception_handler(ReservaTipoInvalido)
async def handle_reserva_tipo_invalido(request: Request, exc: ReservaTipoInvalido):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(ReversionSinInternacion)
async def handle_reversion_sin_internacion(
    request: Request, exc: ReversionSinInternacion
):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(AltaConPasosPendientes)
async def handle_alta_con_pasos_pendientes(
    request: Request, exc: AltaConPasosPendientes
):
    return JSONResponse(
        status_code=409,
        content={
            "detail": str(exc),
            "pasos_pendientes": exc.pasos_pendientes,
        },
    )


@app.exception_handler(ValueError)
async def handle_value_error(request: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


# ------------------------------------------------------------------ #
# Routers
# ------------------------------------------------------------------ #

app.include_router(camas.router)
app.include_router(internaciones.router)


@app.get("/health", tags=["meta"])
async def health():
    return {"status": "ok"}
