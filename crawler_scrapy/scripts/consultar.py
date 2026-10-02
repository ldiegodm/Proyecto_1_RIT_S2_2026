"""Consultador de la base de metadatos (no requiere instalar nada).

No hay que montar ningun servidor SQL: repo/metadatos.sqlite es un archivo que
la pipeline MetadatosSQLitePipeline crea sola en la primera corrida.

Uso:
    python scripts/consultar.py                       # reporte para la defensa
    python scripts/consultar.py esquema               # muestra las tablas y columnas
    python scripts/consultar.py "SELECT ... "         # cualquier SQL
    python scripts/consultar.py --bd otra/ruta.sqlite esquema
"""
import os
import sqlite3
import sys

BD_POR_DEFECTO = os.path.join("repo", "metadatos.sqlite")

REPORTE = [
    ("Resumen del repositorio",
     """SELECT COUNT(*) AS docs,
               SUM(palabras) AS palabras,
               ROUND(SUM(bytes_texto)/1048576.0, 2) AS mb,
               ROUND(SUM(bytes_texto)/1073741824.0, 3) AS gb
          FROM documentos"""),

    ("Reparto por dominio (politica anti-acaparamiento)",
     """SELECT dominio, COUNT(*) AS docs, SUM(palabras) AS palabras,
               ROUND(AVG(densidad_tema), 1) AS densidad_prom
          FROM documentos
         GROUP BY dominio ORDER BY docs DESC LIMIT 20"""),

    ("Profundidad alcanzada (que tan lejos de las semillas sirve el contenido)",
     """SELECT profundidad, COUNT(*) AS docs,
               ROUND(AVG(palabras)) AS palabras_prom,
               ROUND(AVG(densidad_tema), 1) AS densidad_prom
          FROM documentos GROUP BY profundidad ORDER BY profundidad"""),

    ("Dominios nuevos hallados por el crawling enfocado (no eran semilla)",
     """SELECT dominio, COUNT(*) AS docs,
               ROUND(AVG(densidad_tema), 1) AS densidad_prom
          FROM documentos WHERE dominio_semilla = 0
         GROUP BY dominio ORDER BY docs DESC LIMIT 15"""),

    ("Documentos mas densos en el tema",
     """SELECT ROUND(densidad_tema,1) AS densidad, palabras,
               substr(titulo, 1, 55) AS titulo
          FROM documentos ORDER BY densidad_tema DESC LIMIT 10"""),

    ("Fallos de red registrados",
     """SELECT dominio, COUNT(*) AS fallos, MAX(fecha) AS ultimo
          FROM errores GROUP BY dominio ORDER BY fallos DESC LIMIT 10"""),
]


def imprimir(con, titulo, sql):
    print("\n--- {} ---".format(titulo))
    try:
        cur = con.execute(sql)
    except sqlite3.Error as e:
        print("  error de SQL:", e)
        return
    filas = cur.fetchall()
    if not filas:
        print("  (sin datos)")
        return
    cols = [d[0] for d in cur.description]
    anchos = [max(len(str(c)), *(len(str(f[i])) for f in filas))
              for i, c in enumerate(cols)]
    anchos = [min(a, 58) for a in anchos]
    print("  " + "  ".join(str(c).ljust(a) for c, a in zip(cols, anchos)))
    print("  " + "  ".join("-" * a for a in anchos))
    for f in filas:
        print("  " + "  ".join(str(v)[:58].ljust(a)
                               for v, a in zip(f, anchos)))
    print("  ({} fila(s))".format(len(filas)))


def esquema(con):
    for (nombre,) in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"):
        n = con.execute("SELECT COUNT(*) FROM " + nombre).fetchone()[0]
        print("\nTabla {} ({} filas)".format(nombre, n))
        for fila in con.execute("PRAGMA table_info(" + nombre + ")"):
            print("   {:<16} {}".format(fila[1], fila[2]))


def main(argv):
    bd = BD_POR_DEFECTO
    if len(argv) >= 2 and argv[0] == "--bd":
        bd, argv = argv[1], argv[2:]

    if not os.path.exists(bd):
        sys.exit("No existe {}. Corra la arana primero: "
                 "scrapy crawl paleo".format(bd))

    con = sqlite3.connect(bd)
    print("Base: {} ({:.1f} KB)".format(bd, os.path.getsize(bd) / 1024.0))

    if not argv:
        for titulo, sql in REPORTE:
            imprimir(con, titulo, sql)
    elif argv[0] == "esquema":
        esquema(con)
    else:
        imprimir(con, "Consulta", " ".join(argv))
    con.close()


if __name__ == "__main__":
    main(sys.argv[1:])
