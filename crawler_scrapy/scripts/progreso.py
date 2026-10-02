"""Vigila una corrida EN MARCHA sin tocarla (solo lee el repositorio).

Sirve para la corrida que ya esta corriendo, que no tiene la extension de
progreso cargada. Abre el SQLite en modo lectura y muestra una linea cada
intervalo, igual que el aranador propio.

Uso:
    python scripts/progreso.py                 # cada 30 s
    python scripts/progreso.py 60              # cada 60 s
    python scripts/progreso.py 30 otro_repo

Nota: la pipeline hace commit cada 100 documentos, asi que el conteo puede
quedar hasta 100 documentos por detras de la realidad.
"""
import os
import sqlite3
import sys
import time


def leer(ruta):
    """Lectura sin bloquear al escritor (la BD esta en modo WAL)."""
    con = sqlite3.connect("file:{}?mode=ro".format(ruta.replace("\\", "/")),
                          uri=True, timeout=5)
    try:
        docs, palabras, bytes_ = con.execute(
            "SELECT COUNT(*), COALESCE(SUM(palabras),0), "
            "COALESCE(SUM(bytes_texto),0) FROM documentos").fetchone()
        dominios = con.execute(
            "SELECT COUNT(DISTINCT dominio) FROM documentos").fetchone()[0]
        errores = con.execute("SELECT COUNT(*) FROM errores").fetchone()[0]
        ultimo = con.execute(
            "SELECT substr(fecha_descarga, 12, 8), dominio FROM documentos "
            "ORDER BY id DESC LIMIT 1").fetchone() or ("--:--:--", "-")
        return docs, palabras, bytes_, dominios, errores, ultimo
    finally:
        con.close()


def main(intervalo=30.0, repo="repo"):
    ruta = os.path.join(repo, "metadatos.sqlite")
    if not os.path.exists(ruta):
        sys.exit("No existe {}. ¿Ya arranco la arana?".format(ruta))

    print("Vigilando {} cada {:.0f} s. Ctrl+C para salir "
          "(no afecta a la arana).".format(ruta, intervalo), flush=True)
    inicio = time.time()
    docs_ant, t_ant, bytes_ant = None, inicio, 0

    try:
        while True:
            try:
                docs, palabras, bytes_, dominios, errores, ultimo = leer(ruta)
            except sqlite3.Error as e:
                print("[{}] BD ocupada ({})".format(
                    time.strftime("%H:%M:%S"), e), flush=True)
                time.sleep(intervalo)
                continue

            ahora = time.time()
            if docs_ant is None:
                ritmo = ritmo_mb = 0.0
            else:
                dt = max(ahora - t_ant, 1e-9)
                ritmo = (docs - docs_ant) * 60.0 / dt
                ritmo_mb = (bytes_ - bytes_ant) * 3600.0 / dt / 1048576.0
            docs_ant, t_ant, bytes_ant = docs, ahora, bytes_

            faltan = ""
            if ritmo_mb > 1:
                for meta in (1.0, 3.0):
                    restante_gb = meta - bytes_ / 1073741824.0
                    if restante_gb > 0:
                        faltan = " | {:.1f} GB en ~{:.1f} h".format(
                            meta, restante_gb * 1024 / ritmo_mb)
                        break

            print("[{}] {:6.1f} min | docs={} ({:.3f} GB) | {:5.0f} docs/min "
                  "{:6.1f} MB/h | dominios={} errores={} | ultimo: {} {}{}"
                  .format(time.strftime("%H:%M:%S"),
                          (ahora - inicio) / 60.0, docs,
                          bytes_ / 1073741824.0, ritmo, ritmo_mb,
                          dominios, errores, ultimo[0], ultimo[1][:28],
                          faltan), flush=True)
            time.sleep(intervalo)
    except KeyboardInterrupt:
        print("\nFin del monitoreo (la arana sigue corriendo).")


if __name__ == "__main__":
    args = sys.argv[1:]
    main(float(args[0]) if args else 30.0,
         args[1] if len(args) > 1 else "repo")
