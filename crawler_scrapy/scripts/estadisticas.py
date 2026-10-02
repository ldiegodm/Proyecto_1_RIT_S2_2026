"""Estadisticas del repositorio (rubrica, seccion 4 del documento).

Responde exactamente los cinco puntos que pide la rubrica:

  1. tamano total del repositorio   -> en texto y en disco (son distintos)
  2. cantidad de documentos
  3. cantidad de palabras           -> tokens totales de la coleccion
  4. palabras distintas             -> tamano del vocabulario
  5. curva de frecuencia graficada  -> zipf.png (lineal y log-log)

Se calcula SOBRE LOS ARCHIVOS YA RECOLECTADOS: recorre los .txt del
repositorio, tokeniza y cuenta. No estima nada.

Las salidas van a una carpeta propia por arañador, para poder comparar el
repositorio del arañador propio contra el de Scrapy sin mezclar archivos:

    estadisticas_<nombre-del-repo>/
        resumen.txt       <- las cifras, listas para pegar en el documento
        frecuencias.csv   <- palabra,frecuencia (todo el vocabulario)
        zipf.csv          <- rango,palabra,frecuencia (para graficar en Excel)
        zipf.png          <- la curva pedida, lineal y log-log

Uso:
    python scripts/estadisticas.py                      # usa repo/
    python scripts/estadisticas.py repo scrapy          # etiqueta explicita
    python scripts/estadisticas.py ../otro/datos propio
"""
import csv
import os
import re
import sqlite3
import sys
from collections import Counter

# Palabra: letras, con guiones y apostrofes internos (T-rex, Romer's)
PALABRA = re.compile(r"[a-záéíóúüñ]+(?:[-'][a-záéíóúüñ]+)*", re.IGNORECASE)


def recorrer(base):
    for raiz, _dirs, archivos in os.walk(base):
        for nombre in archivos:
            if nombre.endswith(".txt"):
                yield os.path.join(raiz, nombre)


def tamano_en_disco(base):
    """Bytes que ocupa de verdad, redondeando cada archivo al bloque de 4 KB.

    NTFS asigna espacio en bloques: 37 mil archivos de 8 KB ocupan mas de lo
    que suman. El explorador de Windows muestra este numero, no el otro.
    """
    bloque, total = 4096, 0
    for ruta in recorrer(base):
        tam = os.path.getsize(ruta)
        total += -(-tam // bloque) * bloque if tam else bloque
    return total


def main(base="repo", etiqueta=None):
    if not os.path.isdir(base):
        sys.exit("No existe la carpeta " + base)

    etiqueta = etiqueta or os.path.basename(os.path.abspath(base))
    salida = "estadisticas_" + etiqueta
    os.makedirs(salida, exist_ok=True)

    frecuencias = Counter()
    docs = bytes_texto = palabras_totales = 0
    por_dominio = Counter()

    print("Recorriendo {} ...".format(base), flush=True)
    for ruta in recorrer(base):
        docs += 1
        bytes_texto += os.path.getsize(ruta)
        # repo/<dominio>/<xx>/<hash>.txt  ->  el dominio es dos niveles arriba
        por_dominio[os.path.basename(os.path.dirname(os.path.dirname(ruta)))] += 1
        with open(ruta, encoding="utf-8", errors="replace") as f:
            tokens = PALABRA.findall(f.read().lower())
        palabras_totales += len(tokens)
        frecuencias.update(tokens)
        if docs % 2000 == 0:
            print("  ... {:,} documentos".format(docs), flush=True)

    if not docs:
        sys.exit("No se encontraron archivos .txt en " + base)

    ordenadas = frecuencias.most_common()
    en_disco = tamano_en_disco(base)

    lineas = []
    w = lineas.append
    w("ESTADISTICAS DEL REPOSITORIO - {}".format(etiqueta))
    w("=" * 58)
    w("Carpeta analizada            : {}".format(os.path.abspath(base)))
    w("")
    w("1. Tamano total del repositorio")
    w("     texto plano (suma de archivos) : {:,} bytes = {:.2f} MB = {:.3f} GB"
      .format(bytes_texto, bytes_texto / 1048576.0, bytes_texto / 1073741824.0))
    w("     ocupado en disco (bloques 4 KB): {:,} bytes = {:.2f} MB = {:.3f} GB"
      .format(en_disco, en_disco / 1048576.0, en_disco / 1073741824.0))
    w("2. Cantidad de documentos      : {:,}".format(docs))
    w("3. Cantidad de palabras        : {:,}".format(palabras_totales))
    w("4. Palabras distintas          : {:,}".format(len(frecuencias)))
    w("")
    w("Derivados utiles para el analisis")
    w("     palabras por documento    : {:,.0f} (promedio)".format(palabras_totales / docs))
    w("     KB de texto por documento : {:.1f} (promedio)".format(bytes_texto / docs / 1024.0))
    w("     riqueza lexica (distintas/totales): {:.4f}".format(len(frecuencias) / palabras_totales))
    w("     dominios distintos        : {:,}".format(len(por_dominio)))
    w("")
    w("Top 30 palabras de la coleccion")
    for i, (palabra, n) in enumerate(ordenadas[:30], start=1):
        w("   {:>2}. {:<16} {:>10,}  ({:.2f} %)".format(
            i, palabra, n, 100.0 * n / palabras_totales))
    w("")
    w("Top 15 dominios por cantidad de documentos")
    for dominio, n in por_dominio.most_common(15):
        w("   {:<34} {:>7,} ({:.1f} %)".format(dominio, n, 100.0 * n / docs))
    w("")
    w("Cola de la distribucion")
    hapax = sum(1 for _, n in ordenadas if n == 1)
    w("     palabras que aparecen UNA sola vez: {:,} ({:.1f} % del vocabulario)"
      .format(hapax, 100.0 * hapax / len(frecuencias)))

    reporte = "\n".join(lineas)
    print("\n" + reporte)
    with open(os.path.join(salida, "resumen.txt"), "w", encoding="utf-8") as f:
        f.write(reporte + "\n")

    with open(os.path.join(salida, "frecuencias.csv"), "w", newline="",
              encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(["palabra", "frecuencia"])
        wr.writerows(ordenadas)

    with open(os.path.join(salida, "zipf.csv"), "w", newline="",
              encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(["rango", "palabra", "frecuencia"])
        for i, (palabra, n) in enumerate(ordenadas, start=1):
            wr.writerow([i, palabra, n])

    graficar(ordenadas, salida, etiqueta)
    resumen_bd(os.path.join(base, "metadatos.sqlite"))
    print("\nArchivos escritos en {}/".format(salida))


def graficar(ordenadas, salida, etiqueta):
    """La curva que pide la rubrica: frecuencia contra rango."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("\n(matplotlib no instalado: grafique zipf.csv en Excel)")
        return

    rangos = range(1, len(ordenadas) + 1)
    valores = [n for _, n in ordenadas]

    fig, ejes = plt.subplots(1, 2, figsize=(12.5, 4.8))

    ejes[0].plot(rangos, valores, linewidth=1.1, color="#0E7560")
    ejes[0].set_title("Frecuencia de palabras (escala lineal)")
    ejes[0].set_xlabel("rango de la palabra")
    ejes[0].set_ylabel("frecuencia")
    ejes[0].grid(alpha=.25, linewidth=.6)

    ejes[1].loglog(rangos, valores, linewidth=1.1, color="#0E7560",
                   label="colección")
    # Referencia de Zipf: f = C / r, anclada en la palabra mas frecuente
    ideal = [valores[0] / r for r in rangos]
    ejes[1].loglog(rangos, ideal, linewidth=1, linestyle="--", color="#96601A",
                   label="Zipf ideal  f = C/r")
    ejes[1].set_title("Ley de Zipf (log-log)")
    ejes[1].set_xlabel("rango (log)")
    ejes[1].set_ylabel("frecuencia (log)")
    ejes[1].grid(alpha=.25, which="both", linewidth=.6)
    ejes[1].legend(frameon=False, fontsize=9)

    # Anotar las tres primeras palabras, que es lo que vuelve legible la curva
    for i in range(min(3, len(ordenadas))):
        ejes[1].annotate(ordenadas[i][0], (i + 1, valores[i]),
                         textcoords="offset points", xytext=(6, 4), fontsize=8,
                         color="#3C4B48")

    fig.suptitle("Distribución de frecuencias — repositorio {}".format(etiqueta),
                 fontsize=11, y=1.02)
    fig.tight_layout()
    ruta = os.path.join(salida, "zipf.png")
    fig.savefig(ruta, dpi=150, bbox_inches="tight")
    print("\nGrafico guardado en {}".format(ruta))


def resumen_bd(ruta):
    """Datos de los metadatos, si el arañador los guarda en SQLite."""
    if not os.path.exists(ruta):
        return
    con = sqlite3.connect("file:{}?mode=ro".format(ruta.replace("\\", "/")),
                          uri=True)
    try:
        print("\n=== Metadatos (SQLite) ===")
        prof = con.execute(
            "SELECT profundidad, COUNT(*) FROM documentos "
            "GROUP BY profundidad ORDER BY profundidad").fetchall()
        print("Documentos por profundidad:", dict(prof))
        sem = con.execute(
            "SELECT dominio_semilla, COUNT(*) FROM documentos "
            "GROUP BY dominio_semilla").fetchall()
        print("Por origen (1 = dominio semilla, 0 = hallado por la araña):",
              dict(sem))
        print("Errores de red registrados:",
              con.execute("SELECT COUNT(*) FROM errores").fetchone()[0])
    except sqlite3.Error as e:
        print("(no se pudo leer la BD:", e, ")")
    finally:
        con.close()


if __name__ == "__main__":
    args = sys.argv[1:]
    main(args[0] if args else "repo", args[1] if len(args) > 1 else None)
