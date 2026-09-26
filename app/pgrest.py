"""Acceso directo a PostgreSQL con la misma interfaz que PostgREST.

Las apps hablaban con la base a través de PostgREST (el servicio REST de
Supabase): pedían `/rest/v1/tabla?select=...&col=eq.valor` y recibían JSON.
Este módulo entiende esas mismas peticiones y las resuelve con SQL directo, así
que el código de las apps no cambia de forma y ya no hace falta ningún servicio
intermedio.

Lo que se soporta es lo que usan las apps: select con columnas, alias,
conversiones y relaciones incrustadas (`*, estudiantes(nombres)`); filtros
eq/neq/gt/gte/lt/lte/like/ilike/is/in/cs/cd con `not.` y los grupos `or=(...)` y
`and=(...)`; order/limit/offset; conteo exacto; insert (también por lotes),
upsert (merge o ignore), update y delete, con `return=representation|minimal`.

El JSON lo arma PostgreSQL (json_agg), igual que hacía PostgREST, así que las
fechas, los números y los uuid salen con el mismo formato que antes.

Desarrollado por Marco Antonio Posligua San Martín.
"""
import json
import re
import threading
import time
from urllib.parse import urlsplit, parse_qsl, unquote

import psycopg
from psycopg import sql, errors
from psycopg_pool import ConnectionPool

__all__ = ['BaseDirecta', 'SesionDirecta', 'RespuestaDirecta', 'ErrorConsulta']

_OPS = {'eq': '=', 'neq': '<>', 'gt': '>', 'gte': '>=', 'lt': '<', 'lte': '<=',
        'like': 'LIKE', 'ilike': 'ILIKE', 'cs': '@>', 'cd': '<@', 'ov': '&&',
        'match': '~', 'imatch': '~*'}
_ESPECIALES = {'select', 'order', 'limit', 'offset', 'on_conflict', 'columns', 'or', 'and',
               'not.or', 'not.and'}


class ErrorConsulta(Exception):
    """Error con la forma de PostgREST: código HTTP y cuerpo {code,message,details,hint}."""

    def __init__(self, status, code, message, details=None, hint=None):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message
        self.details, self.hint = details, hint

    def cuerpo(self):
        return {'code': self.code, 'message': self.message, 'details': self.details, 'hint': self.hint}


# ------------------------------------------------------------------ esquema
class _Esquema:
    """Columnas, tipos, claves primarias y foráneas del esquema public."""

    def __init__(self, conn):
        cur = conn.execute("""
            select c.relname, a.attname, format_type(a.atttypid, a.atttypmod)
              from pg_attribute a
              join pg_class c on c.oid = a.attrelid
              join pg_namespace n on n.oid = c.relnamespace
             where n.nspname = 'public' and c.relkind in ('r','v','m','p','f')
               and a.attnum > 0 and not a.attisdropped
             order by c.relname, a.attnum""")
        self.cols = {}
        for t, col, tipo in cur.fetchall():
            self.cols.setdefault(t, {})[col] = tipo
        cur = conn.execute("""
            select c.relname, array_agg(a.attname order by array_position(i.indkey, a.attnum))
              from pg_index i
              join pg_class c on c.oid = i.indrelid
              join pg_namespace n on n.oid = c.relnamespace
              join pg_attribute a on a.attrelid = c.oid and a.attnum = any(i.indkey)
             where n.nspname = 'public' and i.indisprimary
             group by c.relname""")
        self.pk = {t: list(cols) for t, cols in cur.fetchall()}
        cur = conn.execute("""
            select con.conname, src.relname, dst.relname,
                   array(select attname from unnest(con.conkey) with ordinality k(n, o)
                         join pg_attribute on attrelid = con.conrelid and attnum = k.n order by k.o),
                   array(select attname from unnest(con.confkey) with ordinality k(n, o)
                         join pg_attribute on attrelid = con.confrelid and attnum = k.n order by k.o)
              from pg_constraint con
              join pg_class src on src.oid = con.conrelid
              join pg_class dst on dst.oid = con.confrelid
              join pg_namespace n on n.oid = src.relnamespace
             where con.contype = 'f' and n.nspname = 'public'""")
        self.fks = [dict(nombre=a, origen=b, destino=c, cols_origen=list(d), cols_destino=list(e))
                    for a, b, c, d, e in cur.fetchall()]

    def tabla(self, t):
        if t not in self.cols:
            raise ErrorConsulta(404, '42P01', f'relation "public.{t}" does not exist')
        return self.cols[t]

    def tipo(self, t, col):
        cols = self.tabla(t)
        if col not in cols:
            raise ErrorConsulta(400, '42703', f'column {t}.{col} does not exist')
        return cols[col]

    def relacion(self, base, otra, pista=None):
        """(tipo, fk): 'uno' si base apunta a otra (devuelve objeto), 'muchos' si otra apunta a base."""
        cand = []
        for fk in self.fks:
            if pista and pista not in (fk['nombre'], *fk['cols_origen']):
                continue
            if fk['origen'] == base and fk['destino'] == otra:
                cand.append(('uno', fk))
            elif fk['origen'] == otra and fk['destino'] == base:
                cand.append(('muchos', fk))
        if not cand:
            raise ErrorConsulta(400, 'PGRST200',
                                f"Could not find a relationship between '{base}' and '{otra}' in the schema cache")
        if len(cand) > 1:
            raise ErrorConsulta(300, 'PGRST201',
                                f"More than one relationship was found for '{base}' and '{otra}'")
        return cand[0]


# ------------------------------------------------------------------ análisis
def _partir(texto, sep=','):
    """Divide por `sep` respetando paréntesis y comillas dobles."""
    partes, nivel, actual, comillas = [], 0, [], False
    for ch in texto:
        if ch == '"':
            comillas = not comillas
        elif not comillas and ch == '(':
            nivel += 1
        elif not comillas and ch == ')':
            nivel -= 1
        if ch == sep and nivel == 0 and not comillas:
            partes.append(''.join(actual)); actual = []
        else:
            actual.append(ch)
    partes.append(''.join(actual))
    return [p.strip() for p in partes if p.strip() != '']


def _lista_in(texto):
    """`(a,"b,c",d)` -> ['a', 'b,c', 'd']"""
    t = texto.strip()
    if t.startswith('(') and t.endswith(')'):
        t = t[1:-1]
    vals = []
    for p in _partir(t):
        if len(p) >= 2 and p[0] == '"' and p[-1] == '"':
            p = p[1:-1].replace('\\"', '"')
        vals.append(p)
    return vals


def _analizar_select(texto):
    """Lista de elementos: ('col', alias, nombre, cast) | ('*',) | ('rel', alias, tabla, pista, interno, hijos)."""
    items = []
    for parte in _partir(texto or '*'):
        m = re.fullmatch(r'(?:([\w]+):)?([\w]+)(?:!([\w]+))?(?:!(inner))?\((.*)\)', parte, re.S)
        if m:
            alias, tabla, pista, interno, hijos = m.groups()
            if pista == 'inner':
                pista, interno = None, 'inner'
            items.append(('rel', alias, tabla, pista, bool(interno), _analizar_select(hijos)))
            continue
        if parte == '*':
            items.append(('*',)); continue
        m = re.fullmatch(r'(?:([\w]+):)?([\w]+)(?:::([\w\s\[\]]+))?', parte)
        if not m:
            raise ErrorConsulta(400, 'PGRST100', f'"failed to parse select parameter ({parte})"')
        alias, nombre, cast = m.groups()
        items.append(('col', alias, nombre, cast))
    return items


class _Constructor:
    """Arma el SQL de una consulta sobre una tabla con alias fijo."""

    def __init__(self, esquema):
        self.e = esquema
        self.params = []
        self.n = 0

    def alias(self):
        self.n += 1
        return f'_t{self.n}'

    # -- proyección --
    def columnas(self, tabla, alias, items):
        partes = []
        for it in items:
            if it[0] == '*':
                partes.append(sql.SQL('{}.*').format(sql.Identifier(alias)))
            elif it[0] == 'col':
                _, al, nombre, cast = it
                self.e.tipo(tabla, nombre)
                expr = sql.SQL('{}.{}').format(sql.Identifier(alias), sql.Identifier(nombre))
                if cast:
                    expr = sql.SQL('({})::{}').format(expr, sql.SQL(cast))
                partes.append(sql.SQL('{} AS {}').format(expr, sql.Identifier(al or nombre)))
            else:
                _, al, otra, pista, _interno, hijos = it
                partes.append(sql.SQL('{} AS {}').format(self.incrustar(tabla, alias, otra, pista, hijos),
                                                         sql.Identifier(al or otra)))
        return sql.SQL(', ').join(partes)

    def incrustar(self, tabla, alias, otra, pista, hijos):
        tipo, fk = self.e.relacion(tabla, otra, pista)
        a2 = self.alias()
        cols = self.columnas(otra, a2, hijos)
        if tipo == 'uno':      # base.fk -> otra.pk : objeto o null
            cond = sql.SQL(' AND ').join(
                sql.SQL('{}.{} = {}.{}').format(sql.Identifier(a2), sql.Identifier(d),
                                                sql.Identifier(alias), sql.Identifier(o))
                for o, d in zip(fk['cols_origen'], fk['cols_destino']))
            return sql.SQL('(SELECT row_to_json(_r) FROM (SELECT {} FROM public.{} {} WHERE {} LIMIT 1) _r)').format(
                cols, sql.Identifier(otra), sql.Identifier(a2), cond)
        cond = sql.SQL(' AND ').join(      # otra.fk -> base.pk : lista
            sql.SQL('{}.{} = {}.{}').format(sql.Identifier(a2), sql.Identifier(o),
                                            sql.Identifier(alias), sql.Identifier(d))
            for o, d in zip(fk['cols_origen'], fk['cols_destino']))
        return sql.SQL("(SELECT coalesce(json_agg(_r), '[]'::json) FROM (SELECT {} FROM public.{} {} WHERE {}) _r)").format(
            cols, sql.Identifier(otra), sql.Identifier(a2), cond)

    # -- filtros --
    def valor(self, tabla, col, crudo):
        tipo = self.e.tipo(tabla, col)
        self.params.append(crudo)
        return sql.SQL('%s::{}').format(sql.SQL(tipo))

    def condicion(self, tabla, alias, col, expr):
        """col + 'op.valor' (con not. opcional) -> SQL"""
        negar = False
        if expr.startswith('not.'):
            negar, expr = True, expr[4:]
        if '.' not in expr:
            raise ErrorConsulta(400, 'PGRST100', f'"failed to parse filter ({expr})"')
        op, val = expr.split('.', 1)
        ref = sql.SQL('{}.{}').format(sql.Identifier(alias), sql.Identifier(col))
        tipo = self.e.tipo(tabla, col)
        if op == 'is':
            v = val.lower()
            if v not in ('null', 'true', 'false', 'unknown', 'not_null'):
                raise ErrorConsulta(400, 'PGRST100', f'"failed to parse filter (is.{val})"')
            txt = {'null': 'NULL', 'true': 'TRUE', 'false': 'FALSE', 'unknown': 'UNKNOWN', 'not_null': 'NOT NULL'}[v]
            c = sql.SQL('{} IS ' + txt).format(ref)
        elif op == 'in':
            vals = _lista_in(val)
            self.params.append(vals)
            base = re.sub(r'\[\]$', '', tipo)
            c = sql.SQL('{} = ANY(%s::{}[])').format(ref, sql.SQL(base))
        elif op in ('like', 'ilike'):
            self.params.append(val.replace('*', '%'))
            c = sql.SQL('{}::text ' + _OPS[op] + ' %s').format(ref)
        elif op in ('match', 'imatch'):
            self.params.append(val)
            c = sql.SQL('{}::text ' + _OPS[op] + ' %s').format(ref)
        elif op in ('cs', 'cd', 'ov'):
            if tipo.endswith('[]') and val.startswith('{') is False and val.startswith('('):
                val = '{' + val[1:-1] + '}'
            self.params.append(val)
            c = sql.SQL('{} ' + _OPS[op] + ' %s::{}').format(ref, sql.SQL(tipo))
        elif op in _OPS:
            c = sql.SQL('{} ' + _OPS[op] + ' {}').format(ref, self.valor(tabla, col, val))
        else:
            raise ErrorConsulta(400, 'PGRST100', f'"failed to parse filter ({op})"')
        return sql.SQL('NOT ({})').format(c) if negar else c

    def grupo_logico(self, tabla, alias, clave, cuerpo):
        """or=(a.eq.1,and(b.gt.2,c.is.null)) -> SQL"""
        negar = clave.startswith('not.')
        union = 'OR' if clave.endswith('or') else 'AND'
        cuerpo = cuerpo.strip()
        if cuerpo.startswith('(') and cuerpo.endswith(')'):
            cuerpo = cuerpo[1:-1]
        partes = []
        for p in _partir(cuerpo):
            m = re.fullmatch(r'(not\.)?(or|and)\((.*)\)', p, re.S)
            if m:
                partes.append(self.grupo_logico(tabla, alias, (m.group(1) or '') + m.group(2), '(' + m.group(3) + ')'))
                continue
            col, resto = p.split('.', 1)
            partes.append(self.condicion(tabla, alias, col, resto))
        c = sql.SQL('(' + f' {union} '.join(['{}'] * len(partes)) + ')').format(*partes) if partes else sql.SQL('TRUE')
        return sql.SQL('NOT {}').format(c) if negar else c

    def filtros(self, tabla, alias, pares):
        conds = []
        for k, v in pares:
            if k in ('or', 'and', 'not.or', 'not.and'):
                conds.append(self.grupo_logico(tabla, alias, k, v))
            elif k in _ESPECIALES:
                continue
            elif '.' in k:
                raise ErrorConsulta(400, 'PGRST108', f'Filtro sobre relación incrustada no soportado: {k}')
            else:
                conds.append(self.condicion(tabla, alias, k, v))
        return sql.SQL(' AND ').join(conds) if conds else sql.SQL('TRUE')

    def orden(self, tabla, alias, texto):
        partes = []
        for p in _partir(texto):
            trozos = p.split('.')
            col = trozos[0]
            self.e.tipo(tabla, col)
            s = sql.SQL('{}.{}').format(sql.Identifier(alias), sql.Identifier(col))
            direccion = 'ASC'
            nulos = ''
            for t in trozos[1:]:
                if t in ('asc', 'desc'):
                    direccion = t.upper()
                elif t == 'nullsfirst':
                    nulos = ' NULLS FIRST'
                elif t == 'nullslast':
                    nulos = ' NULLS LAST'
            partes.append(sql.SQL('{} ' + direccion + nulos).format(s))
        return sql.SQL(', ').join(partes)


# ------------------------------------------------------------------ ejecución
class BaseDirecta:
    """Una base PostgreSQL con su caché de esquema y su grupo de conexiones."""

    def __init__(self, dsn, minimo=1, maximo=8, espera_consulta_ms=30000):
        self._pool = ConnectionPool(dsn, min_size=minimo, max_size=maximo, open=False,
                                    kwargs={'autocommit': True,
                                            'options': f'-c statement_timeout={espera_consulta_ms}'})
        self._abierta = False
        self._candado = threading.Lock()
        self._esquema = None
        self._esquema_t = 0

    def _abrir(self):
        if not self._abierta:
            with self._candado:
                if not self._abierta:
                    self._pool.open(wait=True, timeout=15)
                    self._abierta = True

    def esquema(self, conn, forzar=False):
        if forzar or self._esquema is None or time.time() - self._esquema_t > 300:
            self._esquema = _Esquema(conn)
            self._esquema_t = time.time()
        return self._esquema

    # -- petición al estilo PostgREST --
    def peticion(self, metodo, tabla, pares, cuerpo=None, prefer='', aceptar=''):
        """Devuelve (status, cuerpo_json_o_None, cabeceras)."""
        self._abrir()
        metodo = metodo.upper()
        for intento in (1, 2):
            try:
                with self._pool.connection() as conn:
                    esq = self.esquema(conn, forzar=(intento == 2))
                    return self._ejecutar(conn, esq, metodo, tabla, pares, cuerpo, prefer, aceptar)
            except ErrorConsulta as e:
                if intento == 1 and e.code in ('42703', '42P01', 'PGRST200'):
                    continue            # quizá cambió el esquema: se recarga una vez
                raise
            except errors.UniqueViolation as e:
                raise ErrorConsulta(409, '23505', str(e).split('\n')[0], _detalle(e))
            except errors.ForeignKeyViolation as e:
                raise ErrorConsulta(409, '23503', str(e).split('\n')[0], _detalle(e))
            except errors.NotNullViolation as e:
                raise ErrorConsulta(400, '23502', str(e).split('\n')[0], _detalle(e))
            except errors.UndefinedColumn as e:
                if intento == 1:
                    continue
                raise ErrorConsulta(400, '42703', str(e).split('\n')[0])
            except errors.QueryCanceled as e:
                raise ErrorConsulta(500, '57014', 'canceling statement due to statement timeout')
            except (errors.DataError, errors.InvalidTextRepresentation) as e:
                raise ErrorConsulta(400, e.sqlstate or '22000', str(e).split('\n')[0])
            except psycopg.Error as e:
                raise ErrorConsulta(400 if getattr(e, 'sqlstate', None) else 503,
                                    getattr(e, 'sqlstate', None) or 'PGRST000', str(e).split('\n')[0])

    def _ejecutar(self, conn, esq, metodo, tabla, pares, cuerpo, prefer, aceptar):
        esq.tabla(tabla)
        p = dict(pares)
        pref = {x.split('=')[0].strip(): (x.split('=')[1].strip() if '=' in x else '')
                for x in re.split(r'[,;]', prefer or '') if x.strip()}
        un_objeto = 'vnd.pgrst.object' in (aceptar or '')
        items = _analizar_select(p.get('select', '*'))
        c = _Constructor(esq)
        base = c.alias()
        cab = {}

        if metodo in ('GET', 'HEAD'):
            q = sql.SQL('SELECT {} FROM public.{} {} WHERE {}').format(
                c.columnas(tabla, base, items), sql.Identifier(tabla), sql.Identifier(base),
                c.filtros(tabla, base, pares))
            # los incrustados con !inner obligan a que exista la relación
            for it in items:
                if it[0] == 'rel' and it[4]:
                    q = sql.SQL('SELECT * FROM ({}) _x WHERE _x.{} IS NOT NULL AND _x.{}::text <> {}').format(
                        q, sql.Identifier(it[1] or it[2]), sql.Identifier(it[1] or it[2]), sql.Literal('[]'))
            if 'order' in p:
                q = q + sql.SQL(' ORDER BY ') + c.orden(tabla, base, p['order'])
            if 'limit' in p:
                q = q + sql.SQL(' LIMIT {}').format(sql.Literal(int(p['limit'])))
            if 'offset' in p:
                q = q + sql.SQL(' OFFSET {}').format(sql.Literal(int(p['offset'])))
            final = sql.SQL("SELECT coalesce(json_agg(_s), '[]'::json) FROM ({}) _s").format(q)
            filas = conn.execute(final, c.params).fetchone()[0]
            desde = int(p.get('offset', 0))
            if pref.get('count') in ('exact', 'planned', 'estimated'):
                cc = _Constructor(esq)
                cq = sql.SQL('SELECT count(*) FROM public.{} {} WHERE {}').format(
                    sql.Identifier(tabla), sql.Identifier(base), cc.filtros(tabla, base, pares))
                total = conn.execute(cq, cc.params).fetchone()[0]
                if desde > total:
                    raise ErrorConsulta(416, 'PGRST103', 'Requested range not satisfiable',
                                        f'An offset of {desde} was requested, but there are only {total} rows.')
                cab['Content-Range'] = (f'{desde}-{desde + len(filas) - 1}/{total}' if filas else f'*/{total}')
                # PostgREST contesta 206 (contenido parcial) cuando lo devuelto no es todo
                estado = 206 if (filas and len(filas) < total) or (not filas and desde and total) else 200
            else:
                cab['Content-Range'] = f'{desde}-{desde + len(filas) - 1}/*' if filas else '*/*'
                estado = 200
            return self._forma(estado, filas, un_objeto, cab)

        if metodo == 'POST':
            filas_in = cuerpo if isinstance(cuerpo, list) else [cuerpo or {}]
            if not filas_in:
                return (201, [], cab) if pref.get('return') == 'representation' else (201, None, cab)
            if p.get('columns'):
                cols = [x.strip() for x in p['columns'].split(',')]
            else:
                cols = list(filas_in[0].keys())
                if any(set(f.keys()) != set(cols) for f in filas_in[1:]):
                    raise ErrorConsulta(400, 'PGRST102', 'All object keys must match')
            _columnas_de_escritura(esq, tabla, cols)
            idc = [sql.Identifier(k) for k in cols]
            ins = sql.SQL('INSERT INTO public.{} ({}) SELECT {} FROM json_populate_recordset(NULL::public.{}, %s::json) _j').format(
                sql.Identifier(tabla), sql.SQL(', ').join(idc),
                sql.SQL(', ').join(sql.SQL('_j.{}').format(i) for i in idc), sql.Identifier(tabla))
            c.params.append(json.dumps(filas_in, default=str))
            resolucion = pref.get('resolution')
            if resolucion in ('merge-duplicates', 'ignore-duplicates') or p.get('on_conflict'):
                conflicto = [x.strip() for x in p['on_conflict'].split(',')] if p.get('on_conflict') else esq.pk.get(tabla, [])
                objetivo = sql.SQL(', ').join(sql.Identifier(x) for x in conflicto)
                if resolucion == 'merge-duplicates':
                    act = [k for k in cols if k not in conflicto]
                    if act:
                        ins = ins + sql.SQL(' ON CONFLICT ({}) DO UPDATE SET {}').format(
                            objetivo, sql.SQL(', ').join(sql.SQL('{} = EXCLUDED.{}').format(sql.Identifier(k), sql.Identifier(k)) for k in act))
                    else:
                        ins = ins + sql.SQL(' ON CONFLICT ({}) DO NOTHING').format(objetivo)
                elif resolucion == 'ignore-duplicates':
                    ins = ins + sql.SQL(' ON CONFLICT ({}) DO NOTHING').format(objetivo)
            if resolucion == 'merge-duplicates' and pref.get('return') == 'representation':
                return self._devolver_upsert(conn, c, tabla, ins, items, un_objeto, cab)
            return self._devolver(conn, c, tabla, ins, items, pref, un_objeto, 201, cab)

        if metodo == 'PATCH':
            datos = cuerpo or {}
            if not datos:
                return (200, [], cab) if pref.get('return') == 'representation' else (204, None, cab)
            _columnas_de_escritura(esq, tabla, list(datos))
            asign = sql.SQL(', ').join(sql.SQL('{} = _j.{}').format(sql.Identifier(k), sql.Identifier(k)) for k in datos)
            c.params.append(json.dumps(datos, default=str))
            where = c.filtros(tabla, base, pares)
            upd = sql.SQL('UPDATE public.{} AS {} SET {} FROM json_populate_record(NULL::public.{}, %s::json) _j WHERE {}').format(
                sql.Identifier(tabla), sql.Identifier(base), asign, sql.Identifier(tabla), where)
            return self._devolver(conn, c, tabla, upd, items, pref, un_objeto, 200, cab, alias_ret=base)

        if metodo == 'DELETE':
            where = c.filtros(tabla, base, pares)
            dele = sql.SQL('DELETE FROM public.{} AS {} WHERE {}').format(sql.Identifier(tabla), sql.Identifier(base), where)
            return self._devolver(conn, c, tabla, dele, items, pref, un_objeto, 200, cab, alias_ret=base)

        raise ErrorConsulta(405, 'PGRST117', f'Unsupported HTTP method: {metodo}')

    def _devolver(self, conn, c, tabla, orden_sql, items, pref, un_objeto, ok, cab, alias_ret=None):
        if pref.get('return') == 'representation':
            ret = sql.SQL(' RETURNING {}.*').format(sql.Identifier(alias_ret)) if alias_ret else sql.SQL(' RETURNING *')
            c2 = _Constructor(c.e)
            a = c2.alias()
            cols = c2.columnas(tabla, a, items)
            q = sql.SQL("WITH _m AS ({}{}) SELECT coalesce(json_agg(_s), '[]'::json) FROM (SELECT {} FROM _m {}) _s").format(
                orden_sql, ret, cols, sql.Identifier(a))
            filas = conn.execute(q, c.params + c2.params).fetchone()[0]
            return self._forma(ok if ok != 200 or filas else 200, filas, un_objeto, cab)
        conn.execute(orden_sql, c.params)
        return (201 if ok == 201 else 204), None, cab

    def _devolver_upsert(self, conn, c, tabla, orden_sql, items, un_objeto, cab):
        """Upsert con representación: 201 si insertó alguna fila, 200 si solo actualizó (como PostgREST)."""
        c2 = _Constructor(c.e)
        a = c2.alias()
        cols = c2.columnas(tabla, a, items)
        q = sql.SQL("WITH _m AS ({} RETURNING *, (xmax = 0) AS _pgrest_ins), "
                    "_s AS (SELECT {} FROM _m {}) "
                    "SELECT (SELECT coalesce(json_agg(to_jsonb(_s) - '_pgrest_ins'), '[]'::json) FROM _s), "
                    "(SELECT coalesce(bool_or(_pgrest_ins), false) FROM _m)").format(
            orden_sql, cols, sql.Identifier(a))
        filas, inserto = conn.execute(q, c.params + c2.params).fetchone()
        return self._forma(201 if inserto else 200, filas, un_objeto, cab)

    @staticmethod
    def _forma(status, filas, un_objeto, cab):
        if un_objeto:
            if len(filas) != 1:
                raise ErrorConsulta(406, 'PGRST116', 'JSON object requested, multiple (or no) rows returned',
                                    f'The result contains {len(filas)} rows')
            return status, filas[0], cab
        return status, filas, cab


def _columnas_de_escritura(esq, tabla, cols):
    """Al escribir, una columna desconocida da el mismo error que PostgREST (PGRST204)."""
    conocidas = esq.tabla(tabla)
    for k in cols:
        if k not in conocidas:
            raise ErrorConsulta(400, 'PGRST204', f"Could not find the '{k}' column of '{tabla}' in the schema cache")


def _detalle(e):
    d = getattr(e, 'diag', None)
    return getattr(d, 'message_detail', None) if d else None


# ------------------------------------------------------------------ fachada HTTP
class RespuestaDirecta:
    """Imita lo que las apps leían de `requests.Response`."""

    def __init__(self, status, cuerpo, cabeceras=None):
        self.status_code = status
        self._cuerpo = cuerpo
        self.headers = cabeceras or {}
        self.text = '' if cuerpo is None else json.dumps(cuerpo, ensure_ascii=False)
        self.content = self.text.encode()
        self.ok = status < 400

    def json(self):
        if self._cuerpo is None:
            raise ValueError('Respuesta sin contenido')
        return self._cuerpo

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f'HTTP {self.status_code}: {self.text[:300]}')


class SesionDirecta:
    """Sustituye a `requests.Session` para las URL `.../rest/v1/tabla?...`.

    Las apps construían URL de PostgREST; esta sesión las interpreta y responde
    desde la base, sin red. Cualquier otra URL se rechaza para que no quede
    nada hablando con un servicio REST por descuido."""

    def __init__(self, base):
        self.base = base if isinstance(base, BaseDirecta) else BaseDirecta(base)
        self.headers = {}

    def _pedir(self, metodo, url, params=None, headers=None, json_=None, data=None):
        partes = urlsplit(url)
        m = re.search(r'/rest/v1/(.*)$', partes.path)
        if not m:
            raise ValueError(f'URL no reconocida para la base directa: {url}')
        recurso = unquote(m.group(1)).strip('/')
        pares = parse_qsl(partes.query, keep_blank_values=True)
        if params:
            pares += list(params.items()) if isinstance(params, dict) else list(params)
        pares = [(k, str(v)) for k, v in pares]
        h = {**self.headers, **(headers or {})}
        prefer = h.get('Prefer', '') or h.get('prefer', '')
        aceptar = h.get('Accept', '') or h.get('accept', '')
        if json_ is None and data:
            json_ = json.loads(data)
        if recurso == '':
            return RespuestaDirecta(200, self.base.descripcion())
        try:
            st, cuerpo, cab = self.base.peticion(metodo, recurso, pares, json_, prefer, aceptar)
            return RespuestaDirecta(st, cuerpo, cab)
        except ErrorConsulta as e:
            return RespuestaDirecta(e.status, e.cuerpo())

    def get(self, url, params=None, headers=None, timeout=None, **_):
        return self._pedir('GET', url, params, headers)

    def post(self, url, params=None, headers=None, json=None, data=None, timeout=None, **_):
        return self._pedir('POST', url, params, headers, json, data)

    def patch(self, url, params=None, headers=None, json=None, data=None, timeout=None, **_):
        return self._pedir('PATCH', url, params, headers, json, data)

    def delete(self, url, params=None, headers=None, timeout=None, **_):
        return self._pedir('DELETE', url, params, headers)

    def close(self):
        pass


def _descripcion(self):
    """Lo mínimo de la descripción OpenAPI que alguna app lee: las tablas."""
    self._abrir()
    with self._pool.connection() as conn:
        esq = self.esquema(conn)
    return {'swagger': '2.0', 'paths': {f'/{t}': {} for t in sorted(esq.cols)},
            'definitions': {t: {'properties': {c: {'format': ty} for c, ty in cols.items()}}
                            for t, cols in esq.cols.items()}}


BaseDirecta.descripcion = _descripcion
