"""Aplicación FastAPI de Atlas — tablero de camas (capa de presentación).

La API es una capa FINA: traduce HTTP ↔ servicios de dominio. Sin lógica de negocio propia.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.routers import camas, egresos, internaciones
from domain.discharge_checklist_service import AltaConPasosPendientes
from domain.egreso_service import (
    ChecklistLegalIncompleto,
    EgresoActivoYaExiste,
    EgresoEnEstadoTerminal,
    EgresoNoEncontrado,
    ItemNoEncontrado,
    ItemYaMarcado,
    MedioEgresoDesconocido,
    MotivoDiscrepanciaInvalido,
    SalidaFisicaSinOkAdmin,
)
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


# ─── Egresos ──────────────────────────────────────────────────────── #

@app.exception_handler(EgresoNoEncontrado)
async def handle_egreso_no_encontrado(request: Request, exc: EgresoNoEncontrado):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(ItemNoEncontrado)
async def handle_item_no_encontrado(request: Request, exc: ItemNoEncontrado):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(EgresoActivoYaExiste)
async def handle_egreso_activo_ya_existe(request: Request, exc: EgresoActivoYaExiste):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(EgresoEnEstadoTerminal)
async def handle_egreso_en_estado_terminal(request: Request, exc: EgresoEnEstadoTerminal):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(ItemYaMarcado)
async def handle_item_ya_marcado(request: Request, exc: ItemYaMarcado):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(SalidaFisicaSinOkAdmin)
async def handle_salida_fisica_sin_ok_admin(request: Request, exc: SalidaFisicaSinOkAdmin):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(ChecklistLegalIncompleto)
async def handle_checklist_legal_incompleto(request: Request, exc: ChecklistLegalIncompleto):
    return JSONResponse(
        status_code=409,
        content={"detail": str(exc), "items_pendientes": exc.items_pendientes},
    )


@app.exception_handler(MedioEgresoDesconocido)
async def handle_medio_egreso_desconocido(request: Request, exc: MedioEgresoDesconocido):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(MotivoDiscrepanciaInvalido)
async def handle_motivo_discrepancia_invalido(
    request: Request, exc: MotivoDiscrepanciaInvalido
):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


# ------------------------------------------------------------------ #
# Routers
# ------------------------------------------------------------------ #

app.include_router(camas.router)
app.include_router(internaciones.router)
app.include_router(egresos.router)


@app.get("/health", tags=["meta"])
async def health():
    return {"status": "ok"}
