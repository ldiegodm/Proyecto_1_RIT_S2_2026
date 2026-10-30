"""Quita del repositorio documentos que se guardaron por error (revision manual de falsos positivos).

Borra el archivo .txt y el registro de `documentos`, marca la URL como descartada con el motivo indicado (para que
no se vuelva a descargar) y deja una linea en la bitacora por cada documento quitado.

Uso (con el arañador DETENIDO):
    python quitar_documentos.py --motivo fuera_de_tema:amber_alert --ids 48133 49872 49875
"""

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from arana.arana import RAIZ


def quitar(carpeta_datos, ids, motivo):
    """Devuelve las URLs de los documentos quitados."""
    carpeta = Path(carpeta_datos)
    db = sqlite3.connect(carpeta / "arana.db", timeout=2)
    try:
        db.execute("BEGIN IMMEDIATE")                       # falla si el arañador esta escribiendo
    except sqlite3.OperationalError:
        db.close()
        raise SystemExit("La base esta ocupada: detén el arañador (Ctrl+C) antes de correr este script.")
    marcas = ",".join("?" for _ in ids)
    filas = db.execute(f"SELECT id, url_normalizada, host, ruta_archivo_txt FROM documentos WHERE id IN ({marcas})",
                       ids).fetchall()
    fecha = datetime.now().isoformat(timespec="seconds")
    for doc_id, url, _, ruta in filas:
        db.execute("DELETE FROM documentos WHERE id=?", (doc_id,))
        db.execute("UPDATE urls SET estado='descartado', motivo=?, fecha_proceso=? WHERE url_normalizada=?",
                   (motivo, fecha, url))
    db.execute("COMMIT")
    db.close()
    for _, _, _, ruta in filas:                             # los archivos se borran solo si la base ya se actualizo
        (carpeta / ruta).unlink(missing_ok=True)
    bitacora = carpeta / "logs" / "bitacora.log"
    if bitacora.parent.exists():
        with open(bitacora, "a", encoding="utf-8") as f:
            for doc_id, url, host, _ in filas:
                f.write(f"{fecha.replace('T', ' ')} | manual | {url} | {host} | - | quitado | {motivo} (id={doc_id})\n")
    return [url for _, url, _, _ in filas]


def main():
    ap = argparse.ArgumentParser(description="Quita documentos guardados por error.")
    ap.add_argument("--datos", default=str(RAIZ / "datos"))
    ap.add_argument("--ids", type=int, nargs="+", required=True)
    ap.add_argument("--motivo", required=True, help="queda en la base y en la bitacora, p. ej. fuera_de_tema:amber_alert")
    args = ap.parse_args()
    urls = quitar(args.datos, args.ids, args.motivo)
    for u in urls:
        print("quitado:", u)
    print(f"{len(urls)} de {len(args.ids)} documentos quitados.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
