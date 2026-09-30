"""Estadisticas del repositorio (seccion 3 del documento).

Calcula tamaño total, documentos, palabras, palabras distintas, dominios y descartes, y dibuja la
curva de frecuencia de palabras (rango contra frecuencia, escala log-log; Zipf predice pendiente ~ -1).

Uso:
    python estadisticas.py                  # lee datos/ y escribe datos/estadisticas/
    python estadisticas.py --datos D:/datos
Salida: resumen.md (tablas para pegar en el documento), frecuencias.csv y zipf.png
"""

import argparse
import csv
import sqlite3
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from arana.procesamiento import palabras_de


def contar_lote(rutas):
    """Cuenta las palabras de un grupo de archivos (se ejecuta en procesos aparte)."""
    frecuencias, total = Counter(), 0
    for ruta in rutas:
        palabras = palabras_de(Path(ruta).read_text(encoding="utf-8", errors="replace"))
        frecuencias.update(palabras)
        total += len(palabras)
    return frecuencias, total


def lotes(rutas, tamano=200):
    for i in range(0, len(rutas), tamano):
        yield rutas[i:i + tamano]


def main():
    ap = argparse.ArgumentParser(description="Estadisticas del repositorio de PaleoBusca.")
    ap.add_argument("--datos", default=str(Path(__file__).resolve().parent / "datos"))
    ap.add_argument("--procesos", type=int, default=4)
    args = ap.parse_args()

    carpeta = Path(args.datos)
    repo = carpeta / "repositorio"
    salida = carpeta / "estadisticas"
    salida.mkdir(exist_ok=True)
    rutas = sorted(str(r) for r in repo.rglob("*.txt"))
    if not rutas:
        print("No hay documentos en", repo)
        return 1

    print(f"Contando palabras de {len(rutas)} documentos...", flush=True)
    frecuencias, total_palabras = Counter(), 0
    with ProcessPoolExecutor(max_workers=args.procesos) as pool:
        for i, (f, n) in enumerate(pool.map(contar_lote, lotes(rutas))):
            frecuencias.update(f)
            total_palabras += n
            if i % 50 == 0:
                print(f"  {min((i + 1) * 200, len(rutas))}/{len(rutas)} archivos", flush=True)

    tamano_bytes = sum(Path(r).stat().st_size for r in rutas)
    db = sqlite3.connect(carpeta / "arana.db")
    dominios = db.execute("SELECT dominio, COUNT(*), SUM(tamano_bytes_texto), SUM(num_palabras) "
                          "FROM documentos GROUP BY dominio ORDER BY COUNT(*) DESC").fetchall()
    descartes = db.execute("SELECT substr(motivo, 1, CASE WHEN instr(motivo, ':') > 0 THEN instr(motivo, ':') - 1 "
                           "ELSE length(motivo) END) AS m, COUNT(*) FROM urls WHERE estado='descartado' "
                           "GROUP BY m ORDER BY COUNT(*) DESC").fetchall()
    pendientes = db.execute("SELECT COUNT(*) FROM urls WHERE estado='pendiente'").fetchone()[0]

    lineas = ["## Estadisticas del repositorio", "",
              "| Metrica | Valor |", "|---|---|",
              f"| Tamaño total del texto limpio | {tamano_bytes / 1e9:.3f} GB ({tamano_bytes:,} bytes) |",
              f"| Cantidad de documentos | {len(rutas):,} |",
              f"| Cantidad total de palabras | {total_palabras:,} |",
              f"| Palabras distintas (vocabulario) | {len(frecuencias):,} |",
              f"| Dominios distintos | {len(dominios)} |",
              f"| Documentos descartados | {sum(n for _, n in descartes):,} |",
              f"| URLs aun pendientes | {pendientes:,} |", "",
              "### Descartes por motivo", "", "| Motivo | URLs |", "|---|---|"]
    lineas += [f"| {m or '(sin motivo)'} | {n:,} |" for m, n in descartes]
    lineas += ["", "### Documentos por dominio", "", "| Dominio | Documentos | % docs | MB de texto | Palabras |", "|---|---|---|---|---|"]
    lineas += [f"| {d} | {n:,} | {100 * n / len(rutas):.1f} % | {(b or 0) / 1e6:.1f} | {(w or 0):,} |"
               for d, n, b, w in dominios]
    lineas += ["", "### Palabras mas frecuentes", "", "| Rango | Palabra | Frecuencia |", "|---|---|---|"]
    lineas += [f"| {i} | {p} | {f:,} |" for i, (p, f) in enumerate(frecuencias.most_common(20), 1)]
    (salida / "resumen.md").write_text("\n".join(lineas) + "\n", encoding="utf-8")

    ordenadas = frecuencias.most_common()
    with open(salida / "frecuencias.csv", "w", newline="", encoding="utf-8") as f:
        escritor = csv.writer(f)
        escritor.writerow(["rango", "palabra", "frecuencia"])
        for rango, (palabra, freq) in enumerate(ordenadas[:100_000], 1):
            escritor.writerow([rango, palabra, freq])

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    rangos = range(1, len(ordenadas) + 1)
    plt.figure(figsize=(8, 5.5))
    plt.loglog(rangos, [f for _, f in ordenadas], linewidth=1.2, label="PaleoBusca")
    plt.loglog([1, len(ordenadas)], [ordenadas[0][1], ordenadas[0][1] / len(ordenadas)], "--", color="gray",
               label="Zipf ideal (pendiente -1)")
    plt.xlabel("Rango de la palabra (log)")
    plt.ylabel("Frecuencia (log)")
    plt.title("Frecuencia de palabras en toda la coleccion")
    plt.grid(True, which="both", alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(salida / "zipf.png", dpi=150)

    print("\n".join(lineas[:12]))
    print(f"\nArchivos escritos en {salida}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
