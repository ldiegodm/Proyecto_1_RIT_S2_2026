"""Purga del repositorio los documentos que las politicas actuales vetarian.

Para que sirve: las politicas se fueron afinando DURANTE el aranado (es parte
del trabajo: se descubren falsos positivos viendo lo que entra). Los documentos
que ya estaban guardados cuando se agrego un veto siguen ahi. Este script deja
el repositorio consistente con las politicas finales, y de paso borra archivos
huerfanos (.txt sin fila en la BD, que aparecen si el proceso muere entre la
escritura del archivo y el commit de SQLite).

Correrlo ANTES de generar las estadisticas finales y armar el tar.gz.

Uso:
    python scripts/purgar_vetados.py              # muestra que haria
    python scripts/purgar_vetados.py --aplicar    # borra de verdad
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from paleobusca.policies import dominio_vetado, host_otro_idioma  # noqa: E402


def purgar(repo="repo", aplicar=False):
    bd = os.path.join(repo, "metadatos.sqlite")
    if not os.path.exists(bd):
        sys.exit("No existe " + bd)

    con = sqlite3.connect(bd)
    filas = con.execute("SELECT id, dominio, ruta_archivo FROM documentos").fetchall()

    referenciados, a_borrar = set(), []
    for id_, dominio, ruta in filas:
        if ruta:
            referenciados.add(os.path.normpath(os.path.join(repo, ruta)))
        if dominio_vetado(dominio or "") or host_otro_idioma(dominio or ""):
            a_borrar.append((id_, dominio, ruta))

    por_dominio = {}
    for _, dominio, _ in a_borrar:
        por_dominio[dominio] = por_dominio.get(dominio, 0) + 1
    print("Documentos de dominios vetados: {}".format(len(a_borrar)))
    for dominio, n in sorted(por_dominio.items(), key=lambda kv: -kv[1]):
        print("  {:>5}  {}".format(n, dominio))

    huerfanos = []
    for raiz, _dirs, archivos in os.walk(repo):
        for nombre in archivos:
            if nombre.endswith(".txt"):
                ruta = os.path.normpath(os.path.join(raiz, nombre))
                if ruta not in referenciados:
                    huerfanos.append(ruta)
    print("Archivos huerfanos (.txt sin fila en la BD): {}".format(len(huerfanos)))

    if not aplicar:
        print("\n(simulacion: nada se borro; agregue --aplicar para ejecutar)")
        con.close()
        return

    for id_, _dominio, ruta in a_borrar:
        if ruta:
            archivo = os.path.join(repo, ruta)
            if os.path.exists(archivo):
                os.remove(archivo)
                referenciados.discard(os.path.normpath(archivo))
        con.execute("DELETE FROM documentos WHERE id = ?", (id_,))
    con.commit()

    for ruta in huerfanos:
        os.remove(ruta)

    docs, palabras, bytes_ = con.execute(
        "SELECT COUNT(*), COALESCE(SUM(palabras),0), COALESCE(SUM(bytes_texto),0) "
        "FROM documentos").fetchone()
    con.execute("VACUUM")
    con.close()
    print("\nRepositorio final: {:,} documentos | {:,} palabras | "
          "{:.1f} MB de texto".format(docs, palabras, bytes_ / 1048576.0))


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--aplicar"]
    purgar(args[0] if args else "repo", "--aplicar" in sys.argv)
