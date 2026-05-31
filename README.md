# Zenoxia · Atlas

Módulo del ecosistema clínico Zenoxia. Organiza la ocupación del sanatorio:
gestión de camas, reserva, enroque y validación quirúrgica cruzada.

## Qué es

Atlas es la herramienta donde la capacidad que se va a liberar se vuelve visible,
para que Admisión y los coordinadores **organicen el día en vez de improvisarlo**
— incluido el enroque cuando se está full. No predice nada mágico: junta en un
solo lugar lo que los médicos ya saben y hoy se pierde entre sistema, mensajería
informal y la cabeza del coordinador.

## Las tres capas

El módulo es una sola cosa que registra bien y muestra el mismo dato con tres
lentes:

1. **Organizar el día (capa 1, el corazón).** Estados de cama, reserva, enroque,
   validación quirúrgica cruzada, pases UCI ↔ Internación General.
2. **Proyección (capa 2).** Hace visible la capacidad que se va a liberar:
   altas tempranas y candidatos a mover marcados por el médico.
3. **Análisis (capa 3, producto secundario).** Métricas institucionales leídas
   del log append-only del core (ocupación, giro cama, permanencia, %PD, %PD_AC).

Construcción incremental: capa 1 → 2 → 3.

## Principio de oro

El repo `zenoxia-core` contiene SOLO lo compartido entre módulos. Lo específico
de Atlas vive acá, no en el core. Atlas nunca origina dato clínico (ej.
"candidato a alta"): lo emite el médico en el dominio clínico; Atlas lo lee.

## Stack

Python / FastAPI / SQLAlchemy 2.0 async / PostgreSQL (igual que el core).

## Cómo empezar

Antes de codear, leer `docs/DISENO_MODULO_ATLAS.md` y `CLAUDE.md`.

## Desarrollo local

Atlas tiene base propia (modelo federado: autónoma, no comparte DB con el core).

```bash
# 1. Levantar Postgres
docker compose up -d

# 2. Configurar entorno
cp .env.example .env

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Aplicar migraciones
alembic upgrade head
```

Para generar una migración nueva tras tocar modelos:
```bash
alembic revision --autogenerate -m "descripción del cambio"
```

## Estado

Fase 2 — scaffold inicial. La capa 1 se construye en sesiones siguientes.
