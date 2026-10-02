"""Reencola paginas indice que una corrida anterior descarto antes de existir la regla de indices.

Cuando se cambia un filtro, lo ya descartado no se vuelve a mirar. Este script devuelve a la frontera las URLs que
quedaron como `fuera_de_tema:0.0` (menos de 30 palabras, puntaje no confiable) y que tienen el tema en la ruta, por
ejemplo Category:Pterosaurs_of_Europe: el arañador ahora las trata como indices y sigue sus enlaces.

Uso (con el arañador DETENIDO):
    python reencolar_indices.py
    python reencolar_indices.py --datos D:/datos
"""

import argparse
import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlsplit

from arana import procesamiento as proc
from arana.arana import RAIZ


def reencolar(carpeta_datos, tema):
    """Devuelve cuantas URLs volvieron a pendiente."""
    db = sqlite3.connect(Path(carpeta_datos) / "arana.db", timeout=2)
    try:
        db.execute("BEGIN IMMEDIATE")                       # falla si el arañador esta escribiendo
    except sqlite3.OperationalError:
        db.close()
        raise SystemExit("La base esta ocupada: detén el arañador (Ctrl+C) antes de correr este script.")
    filas = db.execute("SELECT url_normalizada FROM urls WHERE estado='descartado' AND motivo='fuera_de_tema:0.0'")
    urls = [u for (u,) in filas.fetchall() if tema.bonus_anchor(urlsplit(u).path) > 0]
    db.executemany("UPDATE urls SET estado='pendiente', motivo=NULL, fecha_proceso=NULL WHERE url_normalizada=?",
                   [(u,) for u in urls])
    db.execute("COMMIT")
    db.close()
    return len(urls)


def main():
    ap = argparse.ArgumentParser(description="Reencola paginas indice descartadas por tener muy poco texto.")
    ap.add_argument("--datos", default=str(RAIZ / "datos"))
    args = ap.parse_args()
    tema = proc.Tema(RAIZ / "config" / "terminos_tema.txt")
    n = reencolar(args.datos, tema)
    print(f"{n} URLs volvieron a pendiente. Ahora puedes iniciar el arañador.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
