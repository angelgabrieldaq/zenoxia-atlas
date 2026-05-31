# CLAUDE.md — Atlas

> Este archivo da contexto persistente a Claude Code. Se lee al inicio de cada sesión.

---

## Parte de Zenoxia (ecosistema)

Este repo es el módulo **Atlas** del ecosistema clínico Zenoxia: la gestión de
camas. El nombre evoca al que sostiene la carga (la ocupación del sanatorio) y al
atlas como mapa ordenado del espacio.

**Principio de oro del ecosistema:**
> El core (repo zenoxia-core) contiene SOLO lo compartido entre módulos.
> Lo específico de Atlas vive ACÁ, no en el core.

Del core, Atlas CONSUME: Patient, LocationResource (la cama es un LocationResource
tipo CAMA_INTERNACION/UTI/UCO), Episodio (la internación), Traslado (cada movimiento,
incluido cada eslabón de un enroque), HitoTiempo (de donde salen las métricas).

Lo PROPIO de Atlas (vive acá): la máquina de estados de gestión de cama (incluido
"Reservada", que no existe en el core), la lógica de enroque, la validación
quirúrgica cruzada, las fórmulas de métricas, y los roles operativos de gestión.

Atlas NUNCA origina dato clínico (ej. "candidato a alta"): lo emite el médico en el
dominio clínico; Atlas lo lee.

Visión completa del ecosistema: repo zenoxia-core, docs/VISION_ECOSISTEMA_ZENOXIA.md.
Otros módulos: Cordis (guardia), Kairos (quirófanos), ICU (futuro), Gia (en revisión).

---

## Qué es este repo

El módulo que organiza la ocupación del sanatorio. Tres capas sobre el mismo
registro de datos:
1. Organizar el día (corazón): estados, reserva, enroque, validación quirúrgica.
2. Proyección: hacer visible la capacidad que se va a liberar (altas/candidatos
   que el médico marca).
3. Análisis: métricas institucionales leídas del HitoTiempo append-only.

Construcción incremental: capa 1 → 2 → 3.
Stack: Python / FastAPI / SQLAlchemy 2.0 async / PostgreSQL (igual que el core).

Diseño completo: docs/DISENO_MODULO_ATLAS.md. Leerlo antes de codear.

---

## Reglas de trabajo

- Antes de modelar un campo: "¿esto lo usa otro módulo? ¿es dato clínico que emite
  el médico?". Si sí a cualquiera de las dos, no lo origina Atlas.
- Cada capa: rama git propia, probar ANTES de commitear.
- NO commitear ni pushear hasta OK visual explícito.
- git pull al inicio de cada sesión (el repo se trabaja desde varios lados).
- Mostrar git status y diff antes de commitear.
