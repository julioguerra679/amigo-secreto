# 🎁 Amigo Secreto

Aplicación web completa para organizar un sorteo de Amigo Secreto (Secret
Santa) con **FastAPI**, **Jinja2** y **SQLite**.

El sorteo garantiza, por construcción matemática, que **nadie se regala a sí
mismo** y que **no existen regalos cruzados** (si a ti te toca Ana, a Ana no
le tocas tú). Cada participante recibe un link personal y un código de 4
dígitos para consultar su resultado en privado.

[![CI](https://github.com/TU_USUARIO/amigo-secreto/actions/workflows/ci.yml/badge.svg)](https://github.com/TU_USUARIO/amigo-secreto/actions)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

---

## Tabla de contenidos

1. [Características](#1-características)
2. [Inicio rápido](#2-inicio-rápido-3-minutos)
3. [Arquitectura](#3-arquitectura)
4. [Estructura del proyecto](#4-estructura-del-proyecto)
5. [El algoritmo, paso a paso](#5-el-algoritmo-paso-a-paso)
6. [Seguridad](#6-seguridad)
7. [API — referencia y ejemplos](#7-api--referencia-y-ejemplos)
8. [Configuración](#8-configuración)
9. [Tests](#9-tests)
10. [Subir a GitHub](#10-subir-a-github)
11. [Despliegue](#11-despliegue)
12. [Preguntas frecuentes](#12-preguntas-frecuentes)

---

## 1. Características

| | |
|---|---|
| 🎲 **Sorteo correcto por construcción** | Sin autoasignaciones ni regalos cruzados; imposible generar un resultado inválido |
| 🚫 **Exclusiones** | «Ana y Luis son pareja, que no se regalen entre ellos» |
| 🔗 **Link único por persona** | Token aleatorio de 32 bytes; recuperable desde el panel en cualquier momento |
| 🔢 **Código de 4 dígitos** | Segundo factor; guardado con PBKDF2, nunca en claro |
| 🛡️ **Anti fuerza bruta** | Bloqueo temporal tras N intentos fallidos |
| 🔐 **Panel de administrador** | Con contraseña, cookie firmada y expiración de sesión |
| ✅ **Auditoría** | Valida el sorteo y comprueba que es *reproducible* desde su semilla |
| 🔁 **Regeneración** | Vuelve a sortear cuando quieras; queda el historial |
| 📧 **Email opcional** | SMTP configurable; si no lo activas, repartes los links a mano |
| 📄 **Exportación CSV** | Descarga links y códigos para repartirlos |
| 🧪 **125 tests** | Algoritmo, API, seguridad y flujo completo, en CI contra SQLite **y** PostgreSQL |
| 🩺 **Diagnóstico de despliegue** | `/admin/diagnostics` dice qué configuración recibió el servidor, sin filtrar secretos |
| 🐳 **Listo para desplegar** | Docker, Render, Railway, Fly.io, GitHub Actions |

---

## 2. Inicio rápido (3 minutos)

```bash
# 1. Clonar
git clone https://github.com/TU_USUARIO/amigo-secreto.git
cd amigo-secreto

# 2. Entorno virtual
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 3. Dependencias
pip install -r requirements.txt

# 4. Configuración
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"   # pega en SECRET_KEY

# 5. Arrancar
uvicorn app.main:app --reload
```

Abre **http://127.0.0.1:8000**

| Ruta | Para qué |
|---|---|
| `/` | Portada del participante |
| `/admin` | Panel del organizador (contraseña de `.env`, por defecto `admin123`) |
| `/docs` | Documentación interactiva de la API (Swagger UI) |

### Flujo del organizador

1. Entra a `/admin` e introduce la contraseña.
2. **Pega la lista de participantes** (uno por línea):
   ```
   Ana, ana@mail.com
   Luis, luis@mail.com
   Carla
   Diego
   ```
3. *(Opcional)* Añade exclusiones:
   ```
   Ana - Luis       ← ni Ana a Luis, ni Luis a Ana
   Carla -> Diego   ← solo Carla no le regala a Diego
   ```

4. Pulsa **Cargar participantes** → aparece una tabla con el **código** y el
   **link** de cada persona. Descarga el CSV: los códigos solo se muestran
   una vez.
5. Pulsa **Generar sorteo**.
6. Reparte a cada persona su link + su código (por WhatsApp, email, papelito…).

> 💡 **Pega la lista tal cual**, venga de donde venga. La app limpia sola las
> comas finales de cada línea, los caracteres invisibles que inserta WhatsApp
> al copiar listas (`U+2060`, que no se ven pero impiden que los nombres
> cuadren), los espacios duros y los acentos descompuestos de macOS. Al
> emparejar las exclusiones ignora mayúsculas, da igual `_` que espacios, y
> si aun así no encuentra a alguien te sugiere el nombre más parecido y te
> señala **todos** los errores a la vez, no de uno en uno.

### Flujo del participante

1. Abre su link → `https://tu-app/participant/<token>`
2. Escribe su código de 4 dígitos.
3. Ve a quién le regala. 🎁

### Probarlo todo de una vez

Con el servidor levantado, en otra terminal:

```bash
python scripts/demo.py --password admin123
```

Carga 6 participantes, sortea, imprime la tabla, consulta como participante
y audita el resultado.

---

## 3. Arquitectura

### 3.1 Vista general

```
                          ┌──────────────────────────┐
   Participante  ───────► │      NAVEGADOR WEB       │ ◄──────  Organizador
   (link + PIN)           └────────────┬─────────────┘         (contraseña)
                                       │ HTTPS
                                       ▼
        ╔══════════════════════════════════════════════════════════════╗
        ║                    FastAPI  (app/main.py)                    ║
        ║  · Monta /static   · Routers   · Manejadores de error        ║
        ╠══════════════════════════════════════════════════════════════╣
        ║                      CAPA HTTP (routers/)                    ║
        ║  ┌────────────────────────┐  ┌────────────────────────────┐  ║
        ║  │ participant.py         │  │ admin.py                   │  ║
        ║  │  GET  /                │  │  GET/POST /admin/login     │  ║
        ║  │  GET  /participant/{t} │  │  GET  /admin/dashboard     │  ║
        ║  │  POST /participant/{t} │  │  POST /admin/upload_…      │  ║
        ║  │  GET  /api/v1/part…    │  │  POST /admin/generate_…    │  ║
        ║  │                        │  │  GET  /admin/assignments   │  ║
        ║  │                        │  │  GET  /admin/validate      │  ║
        ║  └───────────┬────────────┘  └─────────────┬──────────────┘  ║
        ║              │                             │                 ║
        ║   ┌──────────▼─────────┐        ┌──────────▼──────────┐      ║
        ║   │  security.py       │        │  templating.py      │      ║
        ║   │  PBKDF2 · tokens   │        │  Jinja2 + filtros   │      ║
        ║   │  cookie firmada    │        └─────────────────────┘      ║
        ║   │  rate limiting     │                                     ║
        ║   └────────────────────┘                                     ║
        ╠══════════════════════════════════════════════════════════════╣
        ║                   CAPA DE NEGOCIO (services/)                ║
        ║  ┌──────────────────────┐  ┌──────────────────────────────┐  ║
        ║  │ participants.py      │  │ draws.py                     │  ║
        ║  │  alta · exclusiones  │  │  orquesta sorteo + auditoría  │ ║
        ║  │  parsers · CSV       │  │  historial de sorteos        │  ║
        ║  └──────────┬───────────┘  └──────────────┬───────────────┘  ║
        ║             │                             │                  ║
        ║             │      ┌──────────────────────▼───────────────┐  ║
        ║             │      │ assignment.py    ★ NÚCLEO PURO ★     │  ║
        ║             │      │  ciclo hamiltoniano + backtracking   │  ║
        ║             │      │  validate_assignments() · reproduce()│  ║
        ║             │      │  sin FastAPI, sin ORM: testeable     │  ║
        ║             │      └──────────────────────────────────────┘  ║
        ╠═════════════▼════════════════════════════════════════════════╣
        ║              CAPA DE DATOS (models.py / database.py)         ║
        ║        SQLAlchemy 2.0 ORM  ·  sesión por request             ║
        ╚══════════════════════════════════════════════════════════════╝
                                       │
                                       ▼
                        ┌──────────────────────────────┐
                        │   SQLite  data/amigo.db      │
                        │  participants · exclusions   │
                        │  draws       · assignments   │
                        └──────────────────────────────┘
                                       │
                        ┌──────────────▼───────────────┐
                        │  mailer.py → SMTP (opcional) │
                        └──────────────────────────────┘
```

### 3.2 Modelo de datos

```
   ┌───────────────────────┐          ┌────────────────────────┐
   │     participants      │          │       exclusions       │
   ├───────────────────────┤          ├────────────────────────┤
   │ id            PK      │◄────────┬┤ participant_id    FK   │
   │ name          UNIQUE  │         └┤ excluded_id       FK   │
   │ email                 │          │ UNIQUE(par, excl)      │
   │ pin_hash      PBKDF2  │          └────────────────────────┘
   │ pin_salt              │
   │ access_token  UNIQUE  │          ┌────────────────────────┐
   │ failed_attempts       │          │         draws          │
   │ locked_until          │          ├────────────────────────┤
   │ view_count            │          │ id              PK     │
   │ first_viewed_at       │          │ seed       ← auditoría │
   │ created_at            │          │ algorithm              │
   └──────────┬────────────┘          │ participant_count      │
              │                       │ is_active              │
              │                       │ created_at · notes     │
              │                       └───────────┬────────────┘
              │                                   │
              │        ┌──────────────────────────▼───────────┐
              └───────►│            assignments               │
                       ├──────────────────────────────────────┤
                       │ id                      PK           │
                       │ draw_id                 FK           │
                       │ giver_id                FK           │
                       │ receiver_id             FK           │
                       │ UNIQUE(draw_id, giver_id)    ← R1    │
                       │ UNIQUE(draw_id, receiver_id) ← R1    │
                       └──────────────────────────────────────┘
```

Las dos restricciones `UNIQUE` hacen que **la propia base de datos** rechace
un sorteo en el que alguien regale o reciba dos veces. Es la última línea de
defensa, por debajo del algoritmo y del validador.

### 3.3 Decisiones de diseño

| Decisión | Motivo |
|---|---|
| **Núcleo del algoritmo puro** (`services/assignment.py` no importa FastAPI ni SQLAlchemy) | Se testea en milisegundos, sin servidor ni base de datos, y podría reutilizarse en un script o en otro framework |
| **Ciclo hamiltoniano único** en vez de «barajar y reintentar» | Las reglas se cumplen por construcción, no por comprobación; sin bucles sin cota |
| **Semilla persistida** en cada sorteo | Hace el sorteo *reproducible* y por tanto auditable |
| **Sorteos versionados** (`is_active`) en vez de sobrescribir | Regenerar deja rastro: se sabe cuántas veces se sorteó y con qué semilla |
| **Token largo + PIN corto** | Un PIN de 4 dígitos solo tiene 10 000 combinaciones; el token de 32 bytes aporta la entropía real y el PIN aporta la usabilidad |
| **PIN hasheado, no cifrado** | Ni el administrador de la base de datos puede leer los códigos |
| **SQLite por defecto, PostgreSQL si hace falta** | Un archivo y cero configuración en local; cambiando `DATABASE_URL` se migra a PostgreSQL sin tocar código, que es lo necesario en plataformas con disco efímero. La suite corre en CI contra ambos |
| **Sin CDNs ni JavaScript de terceros** | La app funciona sin conexión a internet y no filtra datos a terceros |
| **Email opcional y degradado** | Un SMTP caído no puede tumbar el sorteo |

---

## 4. Estructura del proyecto

```
amigo-secreto/
├── app/
│   ├── __init__.py
│   ├── main.py                  # FastAPI: montaje, routers, errores, /health
│   ├── config.py                # Settings desde .env (pydantic-settings)
│   ├── database.py              # Engine, sesión por request, PRAGMAs SQLite
│   ├── models.py                # Modelos ORM: Participant, Exclusion, Draw, Assignment
│   ├── schemas.py               # Contratos de la API (Pydantic)
│   ├── security.py              # PBKDF2, tokens, cookie de admin, bloqueos + CLI
│   ├── mailer.py                # Envío SMTP opcional
│   ├── templating.py            # Configuración única de Jinja2
│   ├── textutils.py             # Limpieza de nombres (invisibles, acentos, mayúsculas)
│   ├── links.py                 # Links personales con la dirección pública correcta
│   │
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── participant.py       # Rutas públicas del participante
│   │   └── admin.py             # Panel HTML + API JSON del administrador
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   ├── assignment.py        # ★ ALGORITMO (módulo puro, sin dependencias)
│   │   ├── participants.py      # Alta, exclusiones, parsers, CSV
│   │   └── draws.py             # Orquestación del sorteo y auditoría
│   │
│   ├── templates/
│   │   ├── base.html
│   │   ├── index.html                # Portada
│   │   ├── participant_login.html    # Formulario del código
│   │   ├── participant_result.html   # Revelación del resultado
│   │   ├── admin_login.html
│   │   ├── admin_dashboard.html      # Panel con tabla de asignaciones
│   │   └── error.html
│   │
│   └── static/
│       └── styles.css           # CSS propio, sin frameworks
│
├── tests/
│   ├── conftest.py              # Fixtures: BD limpia por test
│   ├── test_assignment.py       # 40+ tests del algoritmo
│   └── test_api.py              # Flujo end-to-end de la API y del panel
│
├── scripts/
│   ├── demo.py                  # Demo end-to-end con la librería estándar
│   └── admin_password.py        # Generar / verificar el hash del admin (sin dependencias)
│
├── data/
│   └── .gitkeep                 # Aquí vive la BD (ignorada por git)
│
├── .github/workflows/ci.yml     # Tests en 3 versiones de Python + build Docker
├── .env.example                 # Plantilla de configuración
├── .gitignore
├── .dockerignore
├── Dockerfile
├── Procfile                     # Railway / Heroku
├── render.yaml                  # Render plan gratuito (web + PostgreSQL)
├── render-disk.yaml             # Render de pago (SQLite + disco persistente)
├── runtime.txt
├── pyproject.toml               # Configuración de pytest y ruff
├── requirements.txt
├── requirements-dev.txt
├── LICENSE
└── README.md
```

---

## 5. El algoritmo, paso a paso

> Código completo y comentado: [`app/services/assignment.py`](app/services/assignment.py)

### 5.1 Formalizar el problema

Con `n` participantes buscamos una función `f: P → P` que cumpla:

| Regla | Enunciado | Significado |
|:---:|---|---|
| **R1** | `f` es biyectiva | Cada persona regala una vez y recibe una vez |
| **R2** | `f(x) ≠ x` | Nadie se regala a sí mismo |
| **R3** | `f(f(x)) ≠ x` | No hay regalos cruzados A→B y B→A |
| **R4** | `f(x) ∉ Excl(x)` | Se respetan las exclusiones del organizador |

R1 + R2 es lo que en combinatoria se llama un **desarreglo** (*derangement*).
R3 añade que la permutación no tenga **ciclos de longitud 2**.

### 5.2 Por qué NO usamos «barajar y reintentar»

La solución que aparece en casi todos los tutoriales es:

```python
# ❌ NO es lo que hace esta app
while True:
    barajar(lista)
    if nadie_se_toca_a_si_mismo(lista):
        break
```

Problemas reales:

* La probabilidad de que una permutación aleatoria sea un desarreglo tiende a
  **1/e ≈ 36,8 %**. Añadiendo R3 y las exclusiones, la tasa de éxito cae más.
* Con exclusiones densas el bucle **puede no terminar nunca**: no tiene cota.
* Es difícil razonar sobre el sesgo del muestreo.

### 5.3 La idea: un único ciclo

Coloca a las `n` personas en **un solo círculo** y que cada una le regale a la
siguiente:

```
        p₀ ──► p₁ ──► p₂ ──► p₃ ──► p₄
        ▲                             │
        └─────────────────────────────┘

        f(pᵢ) = p₍ᵢ₊₁₎ mod n
```

Con esta construcción:

* **R1 se cumple siempre.** Recorrer un ciclo que visita todos los nodos
  exactamente una vez *es* una biyección.
* **R2 se cumple si n ≥ 2.** El siguiente de `pᵢ` nunca es `pᵢ`.
* **R3 se cumple si n ≥ 3.** Como `f(f(pᵢ)) = p₍ᵢ₊₂₎`, para que
  `p₍ᵢ₊₂₎ = pᵢ` haría falta que `n` divida a 2, es decir `n ≤ 2`.

> **Las tres primeras reglas dejan de ser condiciones que hay que comprobar y
> pasan a ser propiedades estructurales.** Es *imposible* que el algoritmo
> produzca una autoasignación o un regalo cruzado.

Por eso la app **exige un mínimo de 3 participantes**: con 2 personas el
regalo cruzado es matemáticamente inevitable (solo existe A→B y B→A).

### 5.4 Las exclusiones: backtracking aleatorizado

Solo queda R4. Construimos el grafo dirigido de regalos permitidos:

```
   G = (V, E)    V = participantes
                 E = { (a, b) : a ≠ b  y  b ∉ Excl(a) }
```

y buscamos en él un **ciclo hamiltoniano** (un ciclo que pasa por todos los
nodos exactamente una vez):

```
  ENTRADA: ids, exclusiones, semilla

  1. rng ← Random(semilla)            # generador propio, reproducible
  2. inicio ← rng.choice(ids)         # fijar el primer nodo no pierde
     camino ← [inicio]                #   soluciones: cualquier rotación
     usados ← {inicio}                #   del ciclo da la misma f

  3. MIENTRAS el camino no esté vacío:
       a) si len(camino) == n:
              si se puede cerrar (último → inicio no está excluido):
                   ✔ DEVOLVER camino
              si no: retroceder

       b) candidatos ← [x ∈ ids : x ∉ usados y x ∉ Excl(actual)]
          rng.shuffle(candidatos)      ← aquí nace la aleatoriedad

       c) si no quedan candidatos por probar en este nivel:
              retroceder (quitar el último nodo y seguir por otra rama)
          si no:
              avanzar al siguiente candidato

  4. Si se agota la búsqueda → error explícito «no existe asignación válida»
```

La implementación es **iterativa** (pila explícita, no recursión) para no
chocar con el límite de recursión de Python en grupos grandes.

Antes de empezar hay un **chequeo de factibilidad** barato: si alguien tiene
excluidos a todos los demás, o si nadie puede regalarle a X, se falla de
inmediato con un mensaje útil en lugar de buscar en vano.

### 5.5 Complejidad

| Escenario | Coste |
|---|---|
| Sin exclusiones | **O(n)** efectivo — la primera rama siempre funciona; equivale a una barajada de Fisher-Yates, sin reintentos |
| Con exclusiones | Peor caso teórico exponencial (el problema del ciclo hamiltoniano es NP-completo), pero el grafo de un amigo secreto real es densísimo: cada nodo tiene `n-1-|Excl|` salidas y se resuelve al instante. El presupuesto `max_operations` acota el trabajo y falla con un error claro en lugar de colgarse |

En la práctica, con 6 participantes el sorteo se resuelve en **6 pasos** de
búsqueda (lo puedes ver en el campo `attempts` de la respuesta).

### 5.6 Aleatoriedad y reproducibilidad

* Se usa `random.Random(seed)`: una instancia **propia** de Mersenne Twister,
  que no toca el estado global de `random`.
* La semilla aleatoria se genera con `secrets`, no con `random`.
* **Misma semilla + mismos participantes + mismas exclusiones = mismo
  resultado.** La semilla se guarda en la tabla `draws`.
* Por eso el orden de los participantes que recibe el algoritmo es siempre el
  canónico (ids ascendentes); si no, la reproducción fallaría sin motivo.

Esto permite la auditoría del punto 5.8: reproducir el sorteo y comprobar que
coincide con lo guardado.

### 5.7 Sesgo estadístico (honestidad intelectual)

Este método muestrea uniformemente entre los **(n−1)!** ciclos hamiltonianos
posibles, no entre *todos* los desarreglos sin ciclos de 2.

Es una restricción **deliberada y deseable**: un único ciclo garantiza que la
cadena de regalos conecta a todo el grupo y no se parte en subgrupos aislados
(dos tríos que se regalan entre ellos sería un desarreglo válido pero un mal
amigo secreto). Y (n−1)! sigue siendo un espacio enorme: con 10 personas son
**362 880** sorteos distintos.

El validador reporta este caso con el código `not_single_cycle`.

### 5.8 Verificación independiente

`validate_assignments()` audita un resultado **ya generado**, sin asumir cómo
se produjo. El panel lo expone en **Validar sorteo actual** y los tests lo
usan como oráculo.

| Código | Qué detecta |
|---|---|
| `self_gift` | Alguien se asignó a sí mismo (R2) |
| `duplicate_giver` | Alguien aparece como emisor más de una vez (R1) |
| `duplicate_receiver` | Alguien recibe más de un regalo (R1) |
| `missing_giver` / `missing_receiver` | Cobertura incompleta (R1) |
| `reciprocal_pair` | **Regalo cruzado A→B y B→A** (R3) |
| `excluded_pair` | Se violó una exclusión (R4) |
| `not_single_cycle` | El grupo se partió en subgrupos cerrados |
| `not_reproducible` | Las asignaciones guardadas **no** coinciden con las que produce la semilla → alguien tocó la base de datos a mano |
| `participants_changed` | El padrón cambió después del sorteo |

El servicio además valida el sorteo **antes de guardarlo**: si el algoritmo
tuviera un fallo, nunca llegaría a la base de datos.

---

## 6. Seguridad

### Modelo de amenazas y defensas

| Amenaza | Defensa |
|---|---|
| Adivinar el link de otra persona | Token de **32 bytes** (`secrets.token_urlsafe`) ≈ 256 bits de entropía |
| Fuerza bruta sobre el PIN (10 000 combinaciones) | Bloqueo tras `MAX_PIN_ATTEMPTS` intentos durante `PIN_LOCKOUT_MINUTES` |
| Robo de la base de datos | Los PIN están hasheados con **PBKDF2-HMAC-SHA256, 200 000 iteraciones** y salt de 16 bytes por participante |
| Ataques de *timing* | Todas las comparaciones de secretos usan `secrets.compare_digest` |
| Acceso al panel de admin | Contraseña + cookie firmada (`itsdangerous`) con `HttpOnly`, `SameSite=Lax`, `Secure` automático en HTTPS y expiración configurable |
| Contraseña del admin en el servidor | Soporta `ADMIN_PASSWORD_HASH` (PBKDF2); el texto plano solo es para desarrollo |
| CSRF en el panel | `SameSite=Lax` impide que un sitio externo dispare POSTs con la sesión |
| Enumeración de participantes | Un token inexistente devuelve un 404 genérico, sin pistas |
| Filtrado por buscadores | `<meta name="robots" content="noindex, nofollow">` |
| Fuga por email | El correo lleva link y PIN, **nunca** el nombre del asignado |
| Secretos en el repositorio | `.env` está en `.gitignore`; `render.yaml` usa `sync: false` para no versionar credenciales |

### Generar credenciales seguras

```bash
# Clave de firma de sesión
python -m app.security secret-key

# Hash de la contraseña del admin (recomendado en producción)
python -m app.security hash-password "mi-contraseña-larga"
# → pbkdf2_sha256$200000$a1b2…$9f8e…
```

Pega el hash en `ADMIN_PASSWORD_HASH` y **deja `ADMIN_PASSWORD` vacío**.

> ⚠️ `ADMIN_PASSWORD_HASH` espera el **hash**, no la contraseña. Cópialo
> entero, incluidos los símbolos `$` — son separadores del formato, y un hash
> cortado no deja entrar a nadie.

**La forma recomendada — funciona igual en Windows, macOS y Linux:**

```bash
python scripts/admin_password.py generar
```

Te pide la contraseña dos veces sin mostrarla, genera el hash, **lo verifica
antes de dártelo** y te dice cuántos caracteres debe tener lo que pegues.

> ⚠️ **No uses un `python -c "…"` de una sola línea en PowerShell o CMD.**
> Esos shells no interpretan el escape `\$` que necesita bash, así que
> producen un hash con barras invertidas —
> `pbkdf2_sha256\$200000\$…` — que es inválido. Es la causa nº 1 de "generé
> el hash y aun así no entro". Este script evita el problema porque no pasa
> nada por el shell.

**¿Ya pegaste un hash y no funciona?** Compruébalo sin adivinar:

```bash
python scripts/admin_password.py verificar   # ¿este hash es de esta contraseña?
python scripts/admin_password.py revisar     # ¿el formato está bien? (no pide la contraseña)
```

La app, además, **repara sola** los estropicios más comunes del copiar-pegar
(barras invertidas, comillas envolventes, saltos de línea) y avisa de ello en
`/admin/diagnostics`. Y si escribes la contraseña en claro en esa variable, te
deja entrar igualmente y lo registra en los logs — pero queda legible en el
panel de tu plataforma, así que cámbialo por el hash cuando puedas.

### Limitaciones conocidas

* Pensado para grupos de **decenas o pocos cientos** de personas, no para
  escala masiva. El bloqueo anti fuerza bruta se guarda por participante en la
  base de datos, no hay rate limiting global por IP: detrás de un proxy
  público conviene añadir uno (Cloudflare, nginx `limit_req`…).
* El panel muestra los PIN en claro justo tras emitirlos, guardándolos en
  memoria del proceso. Se pierden al reiniciar: es **deliberado**, no queremos
  códigos en claro persistidos en disco.
* SQLite soporta bien la concurrencia de lectura (modo WAL activado) pero no
  está pensado para muchas escrituras simultáneas. Para un amigo secreto es
  más que suficiente.

---

## 7. API — referencia y ejemplos

Documentación interactiva en **`/docs`**.

### Endpoints

| Método | Ruta | Auth | Descripción |
|---|---|:---:|---|
| `GET` | `/` | — | Portada |
| `POST` | `/lookup` | — | Redirige al link personal |
| `GET` | `/participant/{token}` | — | Formulario del código |
| `POST` | `/participant/{token}` | PIN | Muestra el resultado (HTML) |
| `GET` | `/api/v1/participant/{token}?pin=1234` | PIN | Resultado (JSON) |
| `GET` | `/health` | — | Health check; su `version` dice qué código está vivo |
| `GET` | `/admin/diagnostics` | — | Diagnóstico de configuración (no revela secretos) |
| `GET/POST` | `/admin/login` | — | Login del organizador |
| `GET` | `/admin/dashboard` | 🔐 | Panel |
| `POST` | `/admin/upload_participants` | 🔐 | **Cargar participantes** |
| `POST` | `/admin/generate_assignments` | 🔐 | **Generar / regenerar el sorteo** |
| `GET` | `/admin/assignments` | 🔐 | **Ver todas las asignaciones** |
| `GET` | `/admin/validate` | 🔐 | **Validar que no hay regalos cruzados** |
| `GET` | `/admin/participants` | 🔐 | Listar participantes |
| `GET` | `/admin/credentials.csv` | 🔐 | Descargar links y códigos |
| `POST` | `/admin/participants/{id}/reset` | 🔐 | Nuevo código y link |

> **Nota sobre `GET /participant/{code}`:** el `{code}` de la URL es el
> **token del link único**; el código de 4 dígitos se envía aparte (por el
> formulario o el parámetro `?pin=`). Así el link por sí solo no revela nada:
> hacen falta los dos factores.

Autenticación de la API: cabecera **`X-Admin-Password`** (cómodo para scripts)
o la cookie de sesión del navegador.

### Ejemplo 1 — Cargar participantes

```bash
curl -X POST http://127.0.0.1:8000/admin/upload_participants \
  -H "Content-Type: application/json" \
  -H "X-Admin-Password: admin123" \
  -d '{
    "participants": [
      {"name": "Ana",   "email": "ana@example.com"},
      {"name": "Luis",  "email": "luis@example.com"},
      {"name": "Carla"},
      {"name": "Diego"},
      {"name": "Elena"}
    ],
    "exclusions": [
      {"giver": "Ana", "receiver": "Luis", "mutual": true}
    ],
    "replace_existing": true
  }'
```

```jsonc
// 201 Created
{
  "created": 5,
  "exclusions": 2,
  "credentials": [
    {
      "id": 1,
      "name": "Ana",
      "email": "ana@example.com",
      "pin": "1977",                                    // ⚠️ solo se ve una vez
      "link": "http://127.0.0.1:8000/participant/X1_xLkksCzSst7JRmCQgsAdH_H6EYKmNvC4DD0VBgTs"
    }
    // …
  ]
}
```

### Ejemplo 2 — Generar el sorteo

```bash
curl -X POST http://127.0.0.1:8000/admin/generate_assignments \
  -H "Content-Type: application/json" \
  -H "X-Admin-Password: admin123" \
  -d '{"seed": "navidad2026", "notes": "Sorteo oficial"}'
```

```jsonc
// 200 OK
{
  "draw": {
    "id": 1,
    "seed": "navidad2026",
    "algorithm": "single-hamiltonian-cycle-v1",
    "participant_count": 5,
    "is_active": true,
    "created_at": "2026-09-13T19:37:06.476454Z",
    "notes": "Sorteo oficial"
  },
  "assignments": [
    {"giver_id": 5, "giver": "Elena", "receiver_id": 4, "receiver": "Diego"},
    {"giver_id": 4, "giver": "Diego", "receiver_id": 1, "receiver": "Ana"},
    {"giver_id": 1, "giver": "Ana",   "receiver_id": 3, "receiver": "Carla"},
    {"giver_id": 3, "giver": "Carla", "receiver_id": 2, "receiver": "Luis"},
    {"giver_id": 2, "giver": "Luis",  "receiver_id": 5, "receiver": "Elena"}
  ],
  "validation": {"ok": true, "checked": 5, "issues": []},
  "attempts": 5
}
```

Observa que la exclusión mutua Ana↔Luis se respeta (Ana→Carla, Luis→Elena) y
que las asignaciones forman un único círculo:
`Ana → Carla → Luis → Elena → Diego → Ana`.

### Ejemplo 3 — Consultar como participante

```bash
curl "http://127.0.0.1:8000/api/v1/participant/X1_xLkks…BgTs?pin=1977"
```

```json
{"giver": "Ana", "receiver": "Carla", "draw_id": 1}
```

Con un PIN incorrecto:

```json
// 401 Unauthorized
{"detail": "Código incorrecto. Te quedan 4 intento(s)."}
```

### Ejemplo 4 — Auditar el sorteo

```bash
curl http://127.0.0.1:8000/admin/validate -H "X-Admin-Password: admin123"
```

```json
{"ok": true, "checked": 5, "issues": []}
```

Si alguien hubiera manipulado la base de datos:

```json
{
  "ok": false,
  "checked": 5,
  "issues": [
    {
      "code": "reciprocal_pair",
      "message": "Regalo cruzado detectado entre 1 y 2 (se regalan mutuamente).",
      "detail": {"a": 1, "b": 2}
    },
    {
      "code": "not_reproducible",
      "message": "Las asignaciones guardadas no coinciden con las que produce la semilla 'navidad2026'. Es posible que se hayan modificado manualmente."
    }
  ]
}
```

### Ejemplo 5 — Usar el algoritmo desde Python

```python
from app.services.assignment import generate_assignments, validate_assignments

ids = [1, 2, 3, 4, 5, 6]
exclusiones = {1: {2}, 2: {1}}          # Ana y Luis son pareja

resultado = generate_assignments(ids, exclusiones, seed="navidad2026")

print(resultado.cycle)   # (4, 6, 1, 3, 2, 5)
print(resultado.pairs)   # ((4, 6), (6, 1), (1, 3), (3, 2), (2, 5), (5, 4))

informe = validate_assignments(resultado.pairs, ids, exclusiones)
print(informe.ok)        # True
```

### Códigos de estado

| Código | Cuándo |
|---|---|
| `200` / `201` | Todo bien |
| `401` | Contraseña de admin o PIN incorrectos, o participante bloqueado |
| `404` | Token inexistente, o no hay sorteo todavía |
| `409` | Menos de 3 participantes, exclusiones imposibles, o sorteo aún no hecho |
| `422` | Cuerpo de la petición inválido (nombres duplicados, email mal formado…) |

---

## 8. Configuración

Todas las opciones se leen de variables de entorno o del archivo `.env`
(ver [`.env.example`](.env.example)).

| Variable | Por defecto | Descripción |
|---|---|---|
| `APP_NAME` | `Amigo Secreto` | Nombre mostrado en la interfaz |
| `BASE_URL` | `http://127.0.0.1:8000` | URL pública; se usa para construir los links. Si se deja sin configurar, la app deduce la dirección de cada petición para no repartir links rotos — pero los correos sí la necesitan |
| `SECRET_KEY` | *(dev)* | Clave de firma de la cookie de admin. **Cámbiala** |
| `ADMIN_PASSWORD` | `admin123` | Contraseña en claro (solo desarrollo) |
| `ADMIN_PASSWORD_HASH` | *(vacío)* | Hash PBKDF2 de la contraseña (producción) |
| `ADMIN_SESSION_MINUTES` | `120` | Duración de la sesión del panel |
| `DIAGNOSTICS_ENABLED` | `true` | Expone `GET /admin/diagnostics`, que describe la configuración sin revelar secretos |
| `MAX_PIN_ATTEMPTS` | `5` | Intentos antes de bloquear |
| `PIN_LOCKOUT_MINUTES` | `15` | Duración del bloqueo |
| `DATABASE_URL` | `sqlite:///./data/amigo_secreto.db` | Cadena de conexión. Acepta SQLite y PostgreSQL; los formatos `postgres://` y `postgresql://` se adaptan solos al driver instalado |
| `MAIL_ENABLED` | `false` | Activa el envío de correos |
| `SMTP_HOST` / `SMTP_PORT` | — / `587` | Servidor SMTP |
| `SMTP_USER` / `SMTP_PASSWORD` | — | Credenciales SMTP |
| `SMTP_FROM` | — | Remitente, p. ej. `Amigo Secreto <no-reply@x.com>` |
| `SMTP_STARTTLS` / `SMTP_SSL` | `true` / `false` | Modo de cifrado |

### Correo con Gmail

```dotenv
MAIL_ENABLED=true
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=tucorreo@gmail.com
SMTP_PASSWORD=xxxx xxxx xxxx xxxx     # contraseña de aplicación, NO la normal
SMTP_FROM=Amigo Secreto <tucorreo@gmail.com>
SMTP_STARTTLS=true
```

> Gmail exige una **contraseña de aplicación** (Cuenta de Google → Seguridad →
> Verificación en 2 pasos → Contraseñas de aplicaciones).

---

## 9. Tests

```bash
pip install -r requirements-dev.txt

pytest                       # toda la suite (125 tests)
pytest -v                    # detalle test por test
pytest tests/test_assignment.py   # solo el algoritmo
ruff check app tests         # linter
```

Qué se cubre:

* **`test_assignment.py`** — R1, R2, R3 y R4 para grupos de 3 a 60 personas;
  500 sorteos consecutivos validados; reproducibilidad por semilla;
  exclusiones imposibles; y el validador detectando cada tipo de fallo.
* **`test_api.py`** — autenticación del admin (cookie y cabecera), carga de
  participantes, generación y regeneración, consulta del participante,
  bloqueo por fuerza bruta, invalidación de links y el flujo HTML completo
  del panel.

Cada test recibe una base de datos limpia, así que no hay orden implícito ni
estado compartido.

---

## 10. Subir a GitHub

```bash
# 1. Comprueba que no vas a subir secretos
cat .gitignore | grep -E "\.env|\.db"     # deben estar ignorados
git status --short                        # .env NO debe aparecer

# 2. Inicializa el repositorio
git init
git add .
git commit -m "feat: aplicación de Amigo Secreto con FastAPI"

# 3. Crea el repo en GitHub
#    Opción A — con GitHub CLI:
gh repo create amigo-secreto --public --source=. --remote=origin --push

#    Opción B — manual: crea el repo vacío en github.com/new y luego:
git branch -M main
git remote add origin https://github.com/TU_USUARIO/amigo-secreto.git
git push -u origin main
```

### Después del primer push

1. **Actualiza el README**: sustituye `TU_USUARIO` en la URL del badge de CI.
2. **Comprueba GitHub Actions**: pestaña *Actions* → el workflow debe pasar en
   Python 3.10, 3.11 y 3.12 y construir la imagen Docker.
3. **Protege `main`** (Settings → Branches → Add rule): exige que CI pase
   antes de fusionar.
4. **Nunca subas el `.env`.** Si se te escapa un secreto, rótalo de inmediato:
   borrarlo en un commit posterior **no** lo elimina del historial.

---

## 11. Despliegue

### ⚠️ Antes de elegir: dónde vive la base de datos

SQLite es un **archivo**. Eso lo hace ideal en local y en cualquier servidor
con disco propio, pero inservible en plataformas cuyo sistema de archivos es
**efímero**: ahí el archivo se borra en cada redeploy, reinicio o despertar
tras dormirse, y con él se van los PIN y el sorteo entero.

| Plataforma / plan | Sistema de archivos | Qué usar |
|---|---|---|
| Local, VPS, Docker con volumen | Persistente | **SQLite** ✅ |
| Render **free** | Efímero, **sin discos** | **PostgreSQL** |
| Render **starter** (de pago) | Disco persistente | SQLite o PostgreSQL |
| Railway / Fly.io **con volumen** | Persistente | SQLite ✅ |
| Railway / Fly.io **sin volumen** | Efímero | PostgreSQL |

Cambiar de motor **no requiere tocar código**: solo `DATABASE_URL`. La app
reescribe sola el formato que entregan las plataformas
(`postgres://` y `postgresql://` → `postgresql+psycopg://`, el driver
instalado), así que puedes pegar la URL tal cual te la den.

### Opción A — Render.com, plan gratuito *(recomendada para empezar)*

> **¿Te salió `services[0] disks are not supported for free tier services`?**
> Ese error aparece cuando el blueprint declara un disco persistente: Render
> no los ofrece en el plan gratuito. El [`render.yaml`](render.yaml) de este
> repo **ya no usa disco**: en su lugar crea una base **PostgreSQL gratuita**,
> que sí persiste. Si tienes una copia antigua del archivo, actualízala.

El repo incluye [`render.yaml`](render.yaml), así que Render lo configura todo:

1. Entra en [render.com](https://render.com) → **New** → **Blueprint**.
2. Conecta tu repositorio de GitHub.
3. Render lee `render.yaml` y crea **dos recursos**: el servicio web y la base
   de datos `amigo-secreto-db`, ya enlazados por `DATABASE_URL`.
4. Te pedirá las variables marcadas con `sync: false`:
   * `ADMIN_PASSWORD_HASH` → genera el valor con
     `python -m app.security hash-password "tu-contraseña"`
   * `BASE_URL` → `https://amigo-secreto.onrender.com` (tu URL definitiva).
     **No lo dejes vacío**: los links que reciben los participantes se
     construyen con este valor.
5. **Apply** y espera al despliegue.

Dos límites del plan gratuito que conviene tener presentes:

* El servicio **se duerme** tras 15 minutos sin tráfico; la primera visita
  después tarda unos 30 segundos en responder.
* Las bases PostgreSQL gratuitas **expiran 30 días** después de crearse, con
  14 días de gracia para pasarlas a plan de pago antes de que se borren.
  Para un amigo secreto que se resuelve en semanas es de sobra; si lo quieres
  permanente, pasa a la opción B o a un VPS.

### Opción B — Render.com de pago, con SQLite y disco

Si prefieres SQLite y que nada expire, el repo incluye
[`render-disk.yaml`](render-disk.yaml): plan `starter`, disco persistente de
1 GB montado en `/var/data` y `DATABASE_URL=sqlite:////var/data/amigo_secreto.db`
(ojo a las **cuatro** barras: indican ruta absoluta).

Renómbralo a `render.yaml` —Render solo lee ese nombre— y despliega igual que
en la opción A. El servicio ya no se duerme y la base no caduca.

### Opción C — Railway

```bash
npm i -g @railway/cli
railway login
railway init
railway up
```

Configura las variables en el panel (`SECRET_KEY`, `ADMIN_PASSWORD_HASH`,
`BASE_URL`). El [`Procfile`](Procfile) ya define el comando de arranque.

Para la base de datos, elige una de las dos:

* **Volumen** montado en `/app/data` y deja `DATABASE_URL` con el valor SQLite
  por defecto.
* **PostgreSQL**: *New → Database → PostgreSQL*. Railway expone
  `DATABASE_URL` automáticamente y la app la adapta sola.

Sin volumen ni base de datos, Railway también borra el archivo SQLite en cada
despliegue.

### Opción D — Docker (cualquier VPS)

```bash
docker build -t amigo-secreto .

docker run -d --name amigo-secreto \
  -p 8000:8000 \
  --env-file .env \
  -v $(pwd)/data:/app/data \
  --restart unless-stopped \
  amigo-secreto
```

O con `docker-compose.yml`:

```yaml
services:
  app:
    build: .
    ports: ["8000:8000"]
    env_file: .env
    volumes: ["./data:/app/data"]
    restart: unless-stopped
```

### Opción E — Fly.io

```bash
fly launch --no-deploy          # detecta el Dockerfile
fly volumes create data --size 1
fly secrets set SECRET_KEY=… ADMIN_PASSWORD_HASH=… BASE_URL=https://tu-app.fly.dev
fly deploy
```

Añade a `fly.toml`:

```toml
[mounts]
  source = "data"
  destination = "/app/data"
```

### Checklist antes de abrirlo al público

- [ ] `SECRET_KEY` generada con `python -m app.security secret-key`
- [ ] `ADMIN_PASSWORD_HASH` configurado y `ADMIN_PASSWORD` **vacío**
- [ ] `BASE_URL` apunta a la URL pública real con **https** (si no, los links
      que reciben los participantes serán incorrectos)
- [ ] **La base de datos persiste**: o un volumen montado donde vive el
      archivo SQLite, o PostgreSQL. Compruébalo haciendo un redeploy después
      de cargar participantes: si al volver no hay nadie, la estás perdiendo
- [ ] HTTPS activo (Render, Railway y Fly lo dan automáticamente)
- [ ] `/health` responde `{"status": "ok"}`
- [ ] Copia de seguridad del CSV de credenciales en un sitio seguro

---

## 12. Preguntas frecuentes

**¿Por qué hacen falta al menos 3 participantes?**
Con 2 personas solo existe A→B y B→A: el regalo cruzado es matemáticamente
inevitable. La app lo rechaza con un mensaje explícito en lugar de generar un
sorteo que incumple sus propias reglas.

**¿Puedo volver a sortear si alguien ya vio su resultado?**
Sí: **Regenerar sorteo**. Se crea un sorteo nuevo y el anterior queda como
«reemplazado» en el historial. Los links y códigos siguen siendo válidos, así
que hay que avisar a todos de que vuelvan a consultar.

**¿Qué pasa si alguien pierde su código?**
En el panel, botón **Nuevo código y link** junto a esa persona. El link
anterior deja de funcionar inmediatamente.

**¿Y si tengo que añadir a alguien después del sorteo?**
Vuelve a cargar el padrón completo con la persona nueva incluida. Eso borra
los sorteos previos (el validador avisaría con `participants_changed` si no se
hiciera) y emite credenciales nuevas para todos.

**¿El administrador puede ver los resultados?**
Sí, la tabla de asignaciones está en el panel: alguien tiene que poder
verificar que el sorteo salió bien. Lo ideal es que el organizador también
participe y evite mirar, o que un tercero haga de organizador.

**¿Cómo verifico que el sorteo no está amañado?**
Con la semilla pública (visible en el panel y en la página de resultado de
cada participante) cualquiera puede reproducir el sorteo:

```python
from app.services.assignment import reproduce
print(reproduce([1, 2, 3, 4, 5], seed="navidad2026"))
```

Si el resultado no coincide con lo guardado, `GET /admin/validate` lo detecta
con el código `not_reproducible`.

**¿Puedo usar PostgreSQL en vez de SQLite?**
Sí, y en planes gratuitos es lo que hay que hacer. `psycopg[binary]` ya está
en `requirements.txt`: solo cambia `DATABASE_URL`. Puedes pegar la URL tal
como te la dé la plataforma (`postgres://…` o `postgresql://…`), la app la
adapta al driver instalado. No hay que tocar código: todo pasa por el ORM, y
la suite de tests se ejecuta en CI contra **los dos motores**.

Para correr los tests contra PostgreSQL en local:

```bash
TEST_DATABASE_URL=postgresql://postgres@localhost:5432/amigo_test pytest -q
```

**Los links de los participantes dan "no se encuentra el sitio".**
El link apunta a otra dirección. Casi siempre porque `BASE_URL` se quedó sin
configurar al desplegar y valía `http://127.0.0.1:8000` — es decir, el
ordenador de quien abre el enlace. Compruébalo en `/admin/diagnostics`:

```jsonc
"links_de_participantes": {
  "base_que_se_esta_usando": "https://tu-app.onrender.com",
  "BASE_URL_configurada": null,                       // ← sin configurar
  "ejemplo_de_link_generado": "https://tu-app.onrender.com/participant/…",
  "diagnostico": "BASE_URL no está configurada…"
}
```

Desde la versión 1.3.0 la app ya no reparte links rotos: si `BASE_URL` falta,
los construye con la dirección real desde la que estás usando el panel
(leyendo `X-Forwarded-Proto` para que salgan en `https`). Aun así,
**configúrala**: los correos se generan fuera de una petición y sin ella
seguirían apuntando a `127.0.0.1`.

Los links correctos están siempre en el panel, en la columna **Link personal**
de la tabla de participantes. Se reconstruyen desde la base de datos, así que
puedes reenviárselos a quien haga falta sin regenerar nada: **los códigos de 4
dígitos siguen siendo los mismos**.

**Un participante dice que su link no es válido.**
Dos causas posibles:

* Está pegando su **código de 4 dígitos** en la casilla del link. La portada
  ahora lo detecta y se lo explica.
* Volviste a pulsar **Cargar participantes** después de repartir los links. Esa
  acción regenera los tokens de todos, así que los enlaces antiguos mueren.
  Reparte los nuevos desde la columna **Link personal**.

**Al cargar participantes me dice que una exclusión "no existe", pero el nombre
está en la lista.**
Casi siempre son **caracteres invisibles**. Las listas copiadas de WhatsApp
traen `U+2060` (WORD JOINER) delante de algunas líneas: no se ve, no ocupa
espacio, y hace que `Laura` y `⁠Laura` sean textos distintos. Como aparece solo
en algunas líneas, unos nombres cruzan y otros no.

Desde la versión 1.2.0 la app los limpia automáticamente, así que basta con
volver a pegar la lista. Si el mensaje persiste, te dirá el nombre exacto que
falló y te sugerirá el más parecido del listado.

**Desplegué y el panel no acepta ninguna contraseña.**
No adivines: **pregúntale al servidor** abriendo en el navegador

```
https://tu-app.onrender.com/admin/diagnostics
```

Devuelve un JSON que describe la configuración que recibió de verdad, sin
revelar el hash ni la contraseña. Léelo en este orden:

| Campo | Qué significa si falla |
|---|---|
| `version` | Si no es la versión que acabas de subir, **la plataforma sirve código antiguo** o estás mirando otro servicio. Empieza por aquí |
| `base_url_configurada` | Si no coincide con la URL que tienes abierta, apunta a otro sitio y los links de los participantes saldrán mal |
| `acceso_admin.modo` | `hash PBKDF2 ✔`, `…NO es un hash`, o `SIN CONFIGURAR` |
| `acceso_admin.contiene_barra_invertida` | `true` ⇒ el hash se generó con el comando de bash en PowerShell o CMD |
| `acceso_admin.caracteres_del_hash` | Debe ser **64**. Menos ⇒ se cortó al pegarlo |
| `acceso_admin.diagnostico` | Frase en castellano con la conclusión |

Los logs de arranque dicen lo mismo en una línea:

```
Acceso de administrador: hash PBKDF2 ✔                     ← correcto
⚠️  ADMIN_PASSWORD_HASH no parece un hash PBKDF2 …         ← contraseña en claro
🚫 No hay contraseña de administrador configurada …        ← falta la variable
```

Si el diagnóstico dice que el formato es correcto pero la contraseña sigue sin
entrar, entonces ese hash no corresponde a esa contraseña. Compruébalo en tu
máquina con `python scripts/admin_password.py verificar` y, si no coinciden,
genera uno nuevo.

Comprueba también que `ADMIN_PASSWORD` esté **vacío**: mientras exista el hash,
se ignora.

Cuando termines de depurar puedes apagar el endpoint con
`DIAGNOSTICS_ENABLED=false`.

**Cambié el código pero el despliegue se comporta igual.**
Abre `/health`: si el `version` no es el del código que subiste, la plataforma
no ha publicado tus cambios. Causas típicas: los archivos se actualizaron en
tu carpeta local pero **no se hizo `git push`**; el servicio está conectado a
otra rama; o hay **dos servicios** en la plataforma de intentos anteriores y
estás abriendo la URL del viejo. En Render, *Events* te muestra el commit
exacto que está desplegado.

**Me sale `disks are not supported for free tier services` al desplegar.**
Render no ofrece discos persistentes en el plan gratuito. El `render.yaml` de
este repo ya no declara ninguno: usa una base PostgreSQL gratuita. Si tienes
una copia anterior del archivo, sustitúyela. Si quieres SQLite con disco,
necesitas plan de pago → usa [`render-disk.yaml`](render-disk.yaml).

**¿Funciona en móvil?**
Sí, la interfaz es responsive y no depende de JavaScript.

---

## Licencia

MIT — ver [LICENSE](LICENSE).
