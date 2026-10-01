"""Almacen de datos (diapositiva 6: «almacen de datos - control y calendarizacion»).

Guarda en SQLite los metadatos de la seccion 2.5 del documento, el estado de cada URL
(para poder reanudar el arañador) y el texto limpio en archivos .txt.

Como todos los hilos escriben aqui (varios productores y un solo archivo, diapositiva 5),
cada operacion toma un candado y hace su propio commit.
"""

import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

ESQUEMA = """
CREATE TABLE IF NOT EXISTS urls (          -- todas las URLs conocidas y su estado (frontera persistente)
    url_normalizada TEXT PRIMARY KEY,
    url_original    TEXT,
    url_padre       TEXT,
    anchor_text     TEXT,
    semilla_origen  TEXT,
    host            TEXT,
    dominio         TEXT,
    profundidad     INTEGER,
    prioridad       REAL,
    estado          TEXT NOT NULL DEFAULT 'pendiente',   -- pendiente | hecho | descartado
    motivo          TEXT,
    codigo_http     INTEGER,
    intentos        INTEGER,
    error           TEXT,
    duplicado_de    INTEGER,
    fecha_descubierta TEXT,
    fecha_proceso   TEXT
);
CREATE INDEX IF NOT EXISTS idx_urls_estado ON urls(estado);

CREATE TABLE IF NOT EXISTS documentos (    -- un registro por documento guardado (seccion 2.5)
    id INTEGER PRIMARY KEY,
    url_original TEXT, url_normalizada TEXT,
    url_padre TEXT, anchor_text TEXT, semilla_origen TEXT,
    dominio TEXT, host TEXT, profundidad INTEGER,
    prioridad REAL, puntaje_tematico REAL, terminos_encontrados TEXT, es_relevante INTEGER,
    content_type TEXT, tamano_bytes_original INTEGER,
    codigo_http INTEGER, intentos INTEGER, error TEXT,
    fecha_descarga TEXT, last_modified TEXT, etag TEXT, tipo_pagina TEXT,
    titulo TEXT, num_palabras INTEGER, tamano_bytes_texto INTEGER,
    hash_sha256 TEXT UNIQUE,
    robots_permitido INTEGER,
    ruta_archivo_txt TEXT
);
CREATE INDEX IF NOT EXISTS idx_docs_dominio ON documentos(dominio);

CREATE TABLE IF NOT EXISTS dominios (      -- P1: lista blanca
    dominio TEXT PRIMARY KEY, origen_dominio TEXT, estado TEXT, fecha_aprobacion TEXT
);

CREATE TABLE IF NOT EXISTS robots (        -- P10: robots.txt por host
    host TEXT PRIMARY KEY, crawl_delay REAL, acceso TEXT, fecha_robots TEXT
);
"""


def ahora():
    return datetime.now().isoformat(timespec="seconds")


class Almacen:
    def __init__(self, carpeta_datos):
        carpeta = Path(carpeta_datos)
        self.dir_repo = carpeta / "repositorio"
        self.dir_repo.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(carpeta / "arana.db", check_same_thread=False,
                                  isolation_level=None)       # autocommit; los lotes usan BEGIN explicito
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(ESQUEMA)
        self.candado = threading.Lock()

        fila = self.db.execute("SELECT COALESCE(MAX(id), 0), COUNT(*), COALESCE(SUM(tamano_bytes_texto), 0) "
                               "FROM documentos").fetchone()
        self._siguiente_id = fila[0] + 1
        self.n_docs = fila[1]
        self.bytes_texto = fila[2]

    # ---- estado para reanudar ------------------------------------------------

    def cargar_pendientes(self):
        filas = self.db.execute("SELECT url_normalizada, url_original, url_padre, anchor_text, semilla_origen, "
                                "host, dominio, profundidad, prioridad FROM urls WHERE estado='pendiente'")
        return filas.fetchall()

    def cargar_vistas(self):
        return (f[0] for f in self.db.execute("SELECT url_normalizada FROM urls"))

    def docs_por_dominio(self):
        return dict(self.db.execute("SELECT dominio, COUNT(*) FROM documentos GROUP BY dominio"))

    def cargar_dominios(self):
        return {d: e for d, e in self.db.execute("SELECT dominio, estado FROM dominios")}

    # ---- escritura -----------------------------------------------------------

    def registrar_urls(self, filas):
        """filas: (url_normalizada, url_original, url_padre, anchor, semilla, host, dominio, profundidad, prioridad)."""
        if not filas:
            return
        fecha = ahora()
        with self.candado:
            self.db.execute("BEGIN")
            self.db.executemany("INSERT OR IGNORE INTO urls (url_normalizada, url_original, url_padre, anchor_text, "
                                "semilla_origen, host, dominio, profundidad, prioridad, fecha_descubierta) "
                                "VALUES (?,?,?,?,?,?,?,?,?,?)", [f + (fecha,) for f in filas])
            self.db.execute("COMMIT")

    def descartar_pendientes(self, urls, motivo):
        """Marca como descartadas, de una vez, URLs pendientes (al reanudar con filtros nuevos)."""
        if not urls:
            return
        fecha = ahora()
        with self.candado:
            self.db.execute("BEGIN")
            self.db.executemany("UPDATE urls SET estado='descartado', motivo=?, fecha_proceso=? WHERE url_normalizada=?",
                                [(motivo, fecha, u) for u in urls])
            self.db.execute("COMMIT")

    def marcar_url(self, url_normalizada, estado, motivo="", codigo=0, intentos=0, error="", duplicado_de=None):
        with self.candado:
            self.db.execute("UPDATE urls SET estado=?, motivo=?, codigo_http=?, intentos=?, error=?, "
                            "duplicado_de=?, fecha_proceso=? WHERE url_normalizada=?",
                            (estado, motivo, codigo, intentos, error, duplicado_de, ahora(), url_normalizada))

    def guardar_dominio(self, dominio, origen, estado):
        with self.candado:
            self.db.execute("INSERT INTO dominios (dominio, origen_dominio, estado, fecha_aprobacion) "
                            "VALUES (?,?,?,?) ON CONFLICT(dominio) DO UPDATE SET estado=excluded.estado, "
                            "fecha_aprobacion=excluded.fecha_aprobacion", (dominio, origen, estado, ahora()))

    def guardar_robots(self, host, crawl_delay, acceso):
        with self.candado:
            self.db.execute("INSERT OR REPLACE INTO robots VALUES (?,?,?,?)", (host, crawl_delay, acceso, ahora()))

    def existe_hash(self, hash_sha256):
        """Politica P7: id del documento con ese hash, o None."""
        with self.candado:
            fila = self.db.execute("SELECT id FROM documentos WHERE hash_sha256=?", (hash_sha256,)).fetchone()
        return fila[0] if fila else None

    def guardar_documento(self, meta, texto):
        """Escribe el .txt y el registro. Devuelve (id, None) o (None, id_del_duplicado) si el hash ya existe.

        meta: diccionario con las columnas de `documentos` (sin id, ruta ni tamano_bytes_texto).
        """
        datos = texto.encode("utf-8")
        with self.candado:
            doc_id = self._siguiente_id
            self._siguiente_id += 1
        ruta = self.dir_repo / meta["dominio"] / f"{doc_id // 1000:05d}" / f"{doc_id:08d}.txt"
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_bytes(datos)

        meta = dict(meta, id=doc_id, tamano_bytes_texto=len(datos),
                    ruta_archivo_txt=str(ruta.relative_to(self.dir_repo.parent)).replace("\\", "/"))
        columnas = ", ".join(meta)
        marcas = ", ".join("?" for _ in meta)
        with self.candado:
            try:
                self.db.execute(f"INSERT INTO documentos ({columnas}) VALUES ({marcas})", list(meta.values()))
            except sqlite3.IntegrityError:          # otro hilo guardo el mismo texto un instante antes
                ruta.unlink(missing_ok=True)
                fila = self.db.execute("SELECT id FROM documentos WHERE hash_sha256=?",
                                       (meta["hash_sha256"],)).fetchone()
                return None, fila[0] if fila else None
            self.n_docs += 1
            self.bytes_texto += len(datos)
        return doc_id, None

    # ---- P9: revisita ------------------------------------------------------------

    def documentos_a_revisar(self, dias_noticia, dias_estable, limite):
        """Documentos cuya ultima descarga es mas vieja que su periodo de revisita (las noticias cambian antes)."""
        corte_noticia = (datetime.now() - timedelta(days=dias_noticia)).isoformat(timespec="seconds")
        corte_estable = (datetime.now() - timedelta(days=dias_estable)).isoformat(timespec="seconds")
        with self.candado:
            return self.db.execute(
                "SELECT id, url_normalizada, url_original, dominio, host, etag, last_modified, semilla_origen, "
                "profundidad, tamano_bytes_texto, ruta_archivo_txt FROM documentos WHERE "
                "(tipo_pagina='noticia' AND fecha_descarga <= ?) OR (tipo_pagina!='noticia' AND fecha_descarga <= ?) "
                "ORDER BY fecha_descarga LIMIT ?", (corte_noticia, corte_estable, limite)).fetchall()

    def tocar_documento(self, doc_id):
        """El servidor dijo 304 (sin cambios): solo se renueva la fecha de descarga."""
        with self.candado:
            self.db.execute("UPDATE documentos SET fecha_descarga=? WHERE id=?", (ahora(), doc_id))

    def actualizar_documento(self, doc_id, ruta_relativa, tamano_anterior, meta, texto):
        """El documento cambio: se reescribe el .txt y se actualizan sus metadatos. False si el hash ya existe."""
        datos = texto.encode("utf-8")
        meta = dict(meta, tamano_bytes_texto=len(datos), fecha_descarga=ahora())
        asignaciones = ", ".join(f"{k}=?" for k in meta)
        with self.candado:
            try:
                self.db.execute(f"UPDATE documentos SET {asignaciones} WHERE id=?", list(meta.values()) + [doc_id])
            except sqlite3.IntegrityError:
                return False
            (self.dir_repo.parent / ruta_relativa).write_bytes(datos)
            self.bytes_texto += len(datos) - tamano_anterior
        return True

    def cerrar(self):
        with self.candado:
            self.db.close()
