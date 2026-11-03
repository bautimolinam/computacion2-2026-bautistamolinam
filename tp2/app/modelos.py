"""Modelos de datos: estados, parámetros de cada tipo de tarea y el registro
interno de una tarea."""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field


class Estado(str, Enum):
    PENDIENTE = "pendiente"
    EJECUTANDO = "ejecutando"
    COMPLETADA = "completada"
    FALLIDA = "fallida"
    CANCELADA = "cancelada"


ESTADOS_FINALES = {Estado.COMPLETADA, Estado.FALLIDA, Estado.CANCELADA}


# ---------------------------------------------------------------- parámetros

class ParamsDescargar(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=500)
    # Solo para la medición 1: si es True, descarga con un for + await
    # (una por vez) en lugar de lanzarlas todas juntas.
    secuencial: bool = False


class ParamsHashear(BaseModel):
    texto: str = Field(max_length=10_000)
    iteraciones: int = Field(gt=0, le=500_000_000)
    # executor   -> ProcessPoolExecutor (lo correcto, es el default)
    # hilo       -> asyncio.to_thread (para comparar: sufre el GIL)
    # bloqueante -> se ejecuta en el event loop (el bug, para la medición 2)
    modo: Literal["executor", "hilo", "bloqueante"] = "executor"


class ParamsEsperar(BaseModel):
    segundos: float = Field(ge=0, le=3600)


class _Base(BaseModel):
    timeout: float | None = Field(
        default=None, gt=0, description="Segundos. Si se omite, usa el default."
    )


class NuevaDescargar(_Base):
    tipo: Literal["descargar"]
    params: ParamsDescargar


class NuevaHashear(_Base):
    tipo: Literal["hashear"]
    params: ParamsHashear


class NuevaEsperar(_Base):
    tipo: Literal["esperar"]
    params: ParamsEsperar


# Unión discriminada: pydantic elige el modelo según el campo "tipo" y valida
# los params correspondientes. Un tipo desconocido devuelve 422.
NuevaTarea = Annotated[
    Union[NuevaDescargar, NuevaHashear, NuevaEsperar],
    Field(discriminator="tipo"),
]


# ------------------------------------------------------------ registro interno

@dataclass
class Tarea:
    tipo: str
    params: dict[str, Any]
    timeout: float
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    estado: Estado = Estado.PENDIENTE
    creada: float = field(default_factory=time.time)
    iniciada: float | None = None
    finalizada: float | None = None
    worker: int | None = None
    resultado: Any = None
    error: str | None = None
    # Motivo que se registra si la tarea termina cancelada (usuario / apagado)
    motivo_cancelacion: str | None = None
    # asyncio.Task que ejecuta el trabajo; solo existe mientras está ejecutando
    handle: asyncio.Task | None = field(default=None, repr=False)

    @property
    def duracion(self) -> float | None:
        if self.iniciada is None or self.finalizada is None:
            return None
        return self.finalizada - self.iniciada

    @property
    def espera_en_cola(self) -> float | None:
        if self.iniciada is None:
            return None
        return self.iniciada - self.creada

    def a_dict(self, con_resultado: bool = True) -> dict[str, Any]:
        d = {
            "id": self.id,
            "tipo": self.tipo,
            "estado": self.estado.value,
            "params": self.params,
            "timeout": self.timeout,
            "creada": self.creada,
            "iniciada": self.iniciada,
            "finalizada": self.finalizada,
            "duracion": self.duracion,
            "worker": self.worker,
            "error": self.error,
        }
        if con_resultado:
            d["resultado"] = self.resultado
        return d
