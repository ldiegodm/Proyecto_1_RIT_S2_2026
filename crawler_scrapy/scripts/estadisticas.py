"""Estadisticas del repositorio (rubrica, seccion 4 del documento).

Recorre los .txt del repositorio y reporta:
  - tamano total del repositorio y cantidad de documentos
  - cantidad de palabras y palabras distintas
  - frecuencias de todas las palabras -> frecuencias.csv
  - datos para la curva de frecuencia (ley de Zipf) -> zipf.csv
  - grafico zipf.png si matplotlib esta instalado

Uso:
    python scripts/estadisticas.py                 # usa repo/
    python scripts/estadisticas.py otra_carpeta
"""
import csv
import os
import re
import sqlite3
import sys
from collections import Counter

PALABRA = re.compile(r"[a-z]+(?:[-'][a-z]+)*")


def recorrer(base):
    for raiz, _dirs, archivos in os.walk(base):
        for nombre in archivos:
            if nombre.endswith(".txt"):
                yield os.path.join(raiz, nombre)


def main(base="repo"):
    frecuencias = Counter()
    docs = bytes_totales = palabras_totales = 0

    for ruta in recorrer(base):
        docs += 1
        bytes_totales += os.path.getsize(ruta)
        with open(ruta, encoding="utf-8", errors="replace") as f:
            tokens = PALABRA.findall(f.read().lower())
        palabras_totales += len(tokens)
        frecuencias.update(tokens)
        if docs % 5000 == 0:
            print("  ... {} documentos procesados".format(docs), flush=True)

    print("\n=== Estadisticas del repositorio: {} ===".format(base))
    print("Documentos           : {:,}".format(docs))
    print("Tamano total         : {:.2f} GB ({:,} bytes)".format(
        bytes_totales / 1073741824.0, bytes_totales))
    print("Palabras (tokens)    : {:,}".format(palabras_totales))
    print("Palabras distintas   : {:,}".format(len(frecuencias)))
    if docs:
        print("Promedio por documento: {:,.0f} palabras / {:.1f} KB".format(
            palabras_totales / docs, bytes_totales / docs / 1024.0))

    ordenadas = frecuencias.most_common()

    with open("frecuencias.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["palabra", "frecuencia"])
        w.writerows(ordenadas)

    with open("zipf.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["rango", "palabra", "frecuencia"])
        for i, (palabra, n) in enumerate(ordenadas, start=1):
            w.writerow([i, palabra, n])

    print("\nTop 25 palabras:")
    for palabra, n in ordenadas[:25]:
        print("  {:<18} {:>10,}".format(palabra, n))

    graficar(ordenadas)
    resumen_bd(os.path.join(base, "metadatos.sqlite"))


def graficar(ordenadas):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("\n(matplotlib no instalado: use zipf.csv para graficar en Excel)")
        return

    rangos = range(1, len(ordenadas) + 1)
    valores = [n for _, n in ordenadas]

    fig, ejes = plt.subplots(1, 2, figsize=(12, 4.5))
    ejes[0].plot(rangos, valores, linewidth=1)
    ejes[0].set_title("Frecuencia de palabras (escala lineal)")
    ejes[0].set_xlabel("rango")
    ejes[0].set_ylabel("frecuencia")
    ejes[1].loglog(rangos, valores, linewidth=1)
    ejes[1].set_title("Ley de Zipf (log-log)")
    ejes[1].set_xlabel("rango (log)")
    ejes[1].set_ylabel("frecuencia (log)")
    fig.tight_layout()
    fig.savefig("zipf.png", dpi=150)
    print("\nGrafico guardado en zipf.png")


def resumen_bd(ruta):
    """Consultas de apoyo para la discusion de resultados."""
    if not os.path.exists(ruta):
        return
    con = sqlite3.connect(ruta)
    print("\n=== Metadatos (SQLite) ===")
    filas = con.execute(
        "SELECT dominio, COUNT(*) n, SUM(palabras) p FROM documentos "
        "GROUP BY dominio ORDER BY n DESC LIMIT 15").fetchall()
    print("{:<34} {:>8} {:>12}".format("dominio", "docs", "palabras"))
    for dominio, n, p in filas:
        print("{:<34} {:>8,} {:>12,}".format(dominio or "-", n, p or 0))
    prof = con.execute(
        "SELECT profundidad, COUNT(*) FROM documentos GROUP BY profundidad "
        "ORDER BY profundidad").fetchall()
    print("\nDocumentos por profundidad:", dict(prof))
    errores = con.execute("SELECT COUNT(*) FROM errores").fetchone()[0]
    print("Errores registrados:", errores)
    con.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "repo")
