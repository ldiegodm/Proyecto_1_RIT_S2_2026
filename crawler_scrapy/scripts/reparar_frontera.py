"""Repara un JOBDIR cuyo proceso murio sin cerrar ordenadamente.

QUE PASO (incidente real del 30/09/2026)
La corrida se corto de golpe (error de descriptores de Windows). Scrapy escribe
los indices de la frontera SOLO al cerrar bien:

  * requests.queue/active.json      -> {slot: [prioridades]} del scheduler
  * requests.queue/<slot>/<pri>/info.json -> {size, head, tail} de cada cola

Sin esos indices, al reanudar Scrapy ve la frontera vacia y cierra de inmediato
con "finished", aunque los datos esten ahi: 223 carpetas de dominio y 424
archivos de cola con decenas de miles de URLs. Y como requests.seen SI se
escribe incrementalmente, esas URLs quedan marcadas como vistas: se perderian
para siempre.

Este script recorre los archivos de cola, cuenta los registros y reconstruye
los dos indices. Formato de queuelib FifoDiskQueue: cada registro es un
encabezado de 4 bytes big-endian con el tamano, seguido del payload.

Uso:
    python scripts/reparar_frontera.py estado/corrida2
"""
import hashlib
import json
import os
import struct
import sys

CHUNKSIZE = 100000          # el valor con el que Scrapy crea las colas
CABECERA = struct.Struct(">L")


def contar_registros(ruta):
    """Cuenta registros completos y devuelve (cantidad, bytes_utiles).

    Un proceso muerto a mitad de una escritura puede dejar un registro
    truncado al final; no se cuenta, y se reporta para poder truncarlo.
    """
    total = os.path.getsize(ruta)
    n, pos = 0, 0
    with open(ruta, "rb") as f:
        while pos < total:
            cab = f.read(CABECERA.size)
            if len(cab) < CABECERA.size:
                break
            (tam,) = CABECERA.unpack(cab)
            if pos + CABECERA.size + tam > total:
                break                      # registro truncado: se descarta
            f.seek(tam, os.SEEK_CUR)
            pos += CABECERA.size + tam
            n += 1
    return n, pos


def reparar(jobdir):
    dq = os.path.join(jobdir, "requests.queue")
    if not os.path.isdir(dq):
        sys.exit("No existe " + dq)

    activos, total_urls, truncados, sospechosos = {}, 0, 0, []

    for nombre in sorted(os.listdir(dq)):
        carpeta = os.path.join(dq, nombre)
        if not os.path.isdir(carpeta):
            continue

        # El nombre es "<slot_legible>-<md5(slot)>": se recupera el slot y se
        # verifica con el hash, que es justamente para evitar colisiones.
        slot, _, firma = nombre.rpartition("-")
        if not slot or hashlib.md5(
                slot.encode("utf8")).hexdigest() != firma:
            sospechosos.append(nombre)
            continue

        prioridades = []
        for sub in sorted(os.listdir(carpeta)):
            subruta = os.path.join(carpeta, sub)
            if not (os.path.isdir(subruta) and sub.lstrip("-").isdigit()):
                continue

            chunks = sorted(n for n in os.listdir(subruta)
                            if n.startswith("q") and n[1:].isdigit())
            if not chunks:
                continue

            registros, utiles = 0, 0
            for chunk in chunks:
                n, pos = contar_registros(os.path.join(subruta, chunk))
                registros += n
                if chunk == chunks[-1]:
                    utiles = pos
                    if pos != os.path.getsize(os.path.join(subruta, chunk)):
                        truncados += 1
                        # queuelib leeria basura al final: se corta ahi
                        with open(os.path.join(subruta, chunk), "r+b") as f:
                            f.truncate(pos)
            if registros == 0:
                continue

            ultimo = int(chunks[-1][1:])
            info = {
                "chunksize": CHUNKSIZE,
                "size": registros,
                "tail": [0, 0, 0],
                "head": [ultimo, registros % CHUNKSIZE],
            }
            with open(os.path.join(subruta, "info.json"), "w",
                      encoding="utf-8") as f:
                json.dump(info, f)

            prioridades.append(int(sub))
            total_urls += registros

        if prioridades:
            activos[slot] = sorted(prioridades)

    with open(os.path.join(dq, "active.json"), "w", encoding="utf-8") as f:
        json.dump(activos, f)

    print("Frontera reconstruida en {}".format(jobdir))
    print("  dominios (slots) : {}".format(len(activos)))
    print("  URLs recuperadas : {:,}".format(total_urls))
    if truncados:
        print("  colas con un registro truncado al final, ya cortadas: {}"
              .format(truncados))
    if sospechosos:
        print("  carpetas que no se pudieron identificar: {} {}".format(
            len(sospechosos), sospechosos[:5]))
    vistas = os.path.join(jobdir, "requests.seen")
    if os.path.exists(vistas):
        with open(vistas, encoding="utf-8", errors="replace") as f:
            print("  URLs ya visitadas (requests.seen): {:,}".format(
                sum(1 for _ in f)))


if __name__ == "__main__":
    reparar(sys.argv[1] if len(sys.argv) > 1 else "estado/corrida2")
