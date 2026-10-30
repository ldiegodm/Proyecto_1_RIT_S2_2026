"""Devuelve a la frontera URLs que una corrida anterior cerro y que con las politicas actuales si deben visitarse.

Cuando se abre una politica, lo ya procesado no se vuelve a mirar solo. Este script reencola, segun lo que se pida:
    --robots         URLs descartadas por robots.txt en hosts cuyo archivo NO se pudo leer (antes no se seguian
                     redirecciones al pedirlo). Las prohibiciones reales se respetan y no se tocan.
    --umbral X       paginas descartadas como fuera_de_tema con puntaje >= X (P3 con un umbral mas bajo)
    --profundidad N  paginas guardadas a profundidad N, para seguir sus enlaces si P5 ahora permite ir mas lejos.
    --reprocesar-host H --puntaje-min X   paginas guardadas de un host (p. ej. en.wikipedia.org) con puntaje >= X,
                     para recoger enlaces que antes no se seguian (por ejemplo sus referencias academicas).
                     No se guardan dos veces: el arañador las reconoce y solo encola sus enlaces.

Uso (con el arañador DETENIDO):
    python reabrir_frontera.py --robots --umbral 6 --profundidad 6
"""

import argparse
import sqlite3
import sys
from pathlib import Path

from arana.arana import RAIZ


def reabrir(carpeta_datos, robots=False, umbral=None, profundidad=None, host=None, puntaje_min=0.0):
    """Devuelve cuantas URLs volvieron a pendiente por cada motivo."""
    db = sqlite3.connect(Path(carpeta_datos) / "arana.db", timeout=2)
    try:
        db.execute("BEGIN IMMEDIATE")                       # falla si el arañador esta escribiendo
    except sqlite3.OperationalError:
        db.close()
        raise SystemExit("La base esta ocupada: detén el arañador (Ctrl+C) antes de correr este script.")
    grupos = {}
    if robots:
        grupos["robots"] = [u for (u,) in db.execute(
            "SELECT url_normalizada FROM urls WHERE estado='descartado' AND motivo='robots' "
            "AND host IN (SELECT host FROM robots WHERE acceso='prohibido')")]
    if umbral is not None:
        filas = db.execute("SELECT url_normalizada, motivo FROM urls WHERE estado='descartado' AND motivo LIKE 'fuera_de_tema:%'")
        grupos["umbral"] = [u for u, m in filas if float(m.split(":")[1]) >= umbral]
    if profundidad is not None:
        grupos["profundidad"] = [u for (u,) in db.execute(
            "SELECT url_normalizada FROM urls WHERE estado='hecho' AND profundidad=?", (profundidad,))]
    if host is not None:
        grupos["host"] = [u for (u,) in db.execute(
            "SELECT u.url_normalizada FROM urls u JOIN documentos d ON d.url_normalizada = u.url_normalizada "
            "WHERE u.estado='hecho' AND d.host=? AND d.puntaje_tematico >= ?", (host, puntaje_min))]
    for urls in grupos.values():
        db.executemany("UPDATE urls SET estado='pendiente', motivo=NULL, fecha_proceso=NULL WHERE url_normalizada=?",
                       [(u,) for u in urls])
    db.execute("COMMIT")
    db.close()
    return {k: len(v) for k, v in grupos.items()}


def main():
    ap = argparse.ArgumentParser(description="Reencola URLs que las politicas actuales si permiten visitar.")
    ap.add_argument("--datos", default=str(RAIZ / "datos"))
    ap.add_argument("--robots", action="store_true", help="reintentar hosts cuyo robots.txt no se pudo leer")
    ap.add_argument("--umbral", type=float, help="reencolar fuera_de_tema con puntaje >= este valor")
    ap.add_argument("--profundidad", type=int, help="reencolar paginas guardadas a esta profundidad para seguir sus enlaces")
    ap.add_argument("--reprocesar-host", help="reencolar las paginas guardadas de este host para seguir sus enlaces")
    ap.add_argument("--puntaje-min", type=float, default=0.0, help="con --reprocesar-host: solo paginas con este puntaje o mas")
    args = ap.parse_args()
    resultado = reabrir(args.datos, args.robots, args.umbral, args.profundidad, args.reprocesar_host, args.puntaje_min)
    for motivo, n in resultado.items():
        print(f"{motivo}: {n} URLs volvieron a pendiente")
    print("Ahora puedes iniciar el arañador.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
